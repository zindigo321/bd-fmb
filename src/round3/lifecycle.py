"""Supervisor owns a real duplicated process HANDLE before any scenario work.

The worker's numeric handle is used ONLY as DuplicateHandle's source argument,
with the exact worker process handle supplied by multiprocessing. It is never
waited on or terminated in the supervisor's handle table.
"""
import contextlib
import gc
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
import traceback

from src.round2.evidence import KEYS, write, utc


def identity(handle):
    import ctypes
    from ctypes import wintypes
    import win32process
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel.GetProcessId.restype = wintypes.DWORD
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    buf = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buf))
    if not kernel.QueryFullProcessImageNameW(int(handle), 0, buf, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    created = win32process.GetProcessTimes(handle)['CreationTime']
    return dict(pid=int(kernel.GetProcessId(int(handle))), executable=buf.value,
                creation_time=str(created), creation_timestamp=created.timestamp())


def snapshot():
    """Read-only inventory. Never uses its PIDs as termination targets."""
    import win32api
    import win32process
    found = []
    for pid in win32process.EnumProcesses():
        handle = None
        try:
            handle = win32api.OpenProcess(0x1000, False, pid)
            item = identity(handle)
            if Path(item['executable']).name.lower() == 'aguiapplication.exe':
                found.append(item)
        except Exception:
            # Protected unrelated OS processes need not be inspected.
            pass
        finally:
            if handle:
                handle.Close()
    return sorted(found, key=lambda x: x['pid'])


def _worker(conn, ctx, cfg, task, payload, stdout_path, stderr_path):
    import pythoncom
    import win32api
    import win32com.client
    import win32process
    sys.stdout = open(stdout_path, 'w', encoding='utf-8', buffering=1)
    sys.stderr = open(stderr_path, 'w', encoding='utf-8', buffering=1)
    os.environ['MPLCONFIGDIR'] = str(Path(ctx['work'])/'mpl')
    app = root = handle = None
    owned = False
    rc = 1
    detail = dict(task=task, run_id=ctx['run_id'], kind='REAL_STK', quit_requested=False)
    pythoncom.CoInitialize()
    try:
        before = set(win32process.EnumProcesses())
        activation_started = time.time()
        app = win32com.client.DispatchEx(cfg['stk']['progid'])
        detail['activation_elapsed_s'] = time.time() - activation_started
        pid = int(app.ProcessID)
        if pid in before:
            raise RuntimeError('BLOCKED: COM returned a preexisting PID; no cleanup authorized')
        handle = win32api.OpenProcess(0x1000 | 0x100000 | 0x0001, False, pid)
        proof = identity(handle)
        if proof['creation_timestamp'] < activation_started - 0.1:
            raise RuntimeError('BLOCKED: creation predates activation')
        if Path(proof['executable']).resolve() != Path(cfg['stk']['executable']).resolve():
            raise RuntimeError('BLOCKED: executable mismatch')
        root = app.Personality2
        root.Isolate()
        if root.CurrentScenario is not None:
            raise RuntimeError('BLOCKED: instance is not empty; no user scenario touched')
        owned = True
        proof.update(run_id=ctx['run_id'], worker_pid=os.getpid(), source_handle=int(handle),
                     absent_from_before=True, empty_scenario=True,
                     activation_started=activation_started)
        conn.send(dict(type='ownership', proof=proof))
        if not conn.poll(30) or conn.recv() != 'handle_retained':
            raise RuntimeError('Supervisor ownership handshake not completed')
        app.Visible = False
        app.UserControl = False
        if task == 'timeout':
            detail['kind'] = 'CONTROLLED_FAULT_INJECTION'
            conn.send(dict(type='ready_for_timeout'))
            time.sleep(120)
        elif task == 'exception':
            detail['kind'] = 'CONTROLLED_FAULT_INJECTION'
            raise RuntimeError('INJECTED_AFTER_OWNERSHIP')
        elif task == 'normal' or task.startswith('serial'):
            root.NewScenario('R3_' + ctx['run_id'] + '_' + task)
            detail['scenario_created'] = True
        else:
            # All experiment-local COM references die on return, before Quit.
            if task.startswith('r7_'):
                from src.round7.experiments import dispatch
            elif task.startswith('r6_'):
                from src.round6.experiments import dispatch
            elif task.startswith('r4_'):
                from src.round4.experiments import dispatch
            else:
                from .experiments import dispatch
            detail['experiment'] = dispatch(root, ctx, cfg, task, payload)
        rc = 0
    except Exception as exc:
        detail['exception'] = repr(exc)
        traceback.print_exc()
        if task == 'exception' and str(exc) == 'INJECTED_AFTER_OWNERSHIP':
            detail['expected_exception_observed'] = True
            rc = 0
        # traceback frames may own COM objects; clear them before cleanup.
        traceback.clear_frames(exc.__traceback__)
    finally:
        if owned:
            try:
                if root.CurrentScenario is not None:
                    root.CloseScenario()
                root = None
                gc.collect()
                app.Quit()
                detail['quit_requested'] = True
            except Exception as exc:
                detail['quit_error'] = repr(exc)
                rc = 1
        root = None
        app = None
        gc.collect()
        detail['com_interfaces_before_uninitialize'] = pythoncom._GetInterfaceCount()
        pythoncom.CoUninitialize()
        if handle:
            handle.Close()
        write(Path(payload['folder'])/'worker.json', detail)
        conn.close()
    raise SystemExit(rc)


def supervise(ctx, cfg, task, folder, payload=None, timeout_s=120):
    import win32api
    import win32event
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    payload = dict(payload or {}, folder=str(folder))
    out = Path(ctx['work'])/(task + '.stdout.log')
    err = Path(ctx['work'])/(task + '.stderr.log')
    pre = snapshot()
    parent, child = mp.get_context('spawn').Pipe()
    proc = mp.get_context('spawn').Process(target=_worker,
        args=(child, ctx, cfg, task, payload, str(out), str(err)))
    start = time.perf_counter()
    evidence = dict(**{k:ctx[k] for k in KEYS}, task=task, started_utc=utc(),
                    timeout_s=timeout_s, before=pre, stdout=str(out), stderr=str(err),
                    timed_out=False, forced_termination=False, ownership=None,
                    memory_peak_working_set_bytes=0, residual=None)
    handle = None
    timeout_ready = None
    last_progress = 0
    pipe_open = True
    proc.start()
    child.close()
    try:
        while proc.is_alive():
            try:
                has_message = pipe_open and parent.poll(.2)
            except (EOFError, BrokenPipeError):
                pipe_open = False
                has_message = False
            if not pipe_open:
                time.sleep(.05)
            if has_message:
                try:
                    msg = parent.recv()
                except (EOFError, BrokenPipeError):
                    msg = {}
                    pipe_open = False
                if msg.get('type') == 'ownership':
                    proof = msg['proof']
                    # proc.sentinel is the process HANDLE returned by CreateProcess
                    # in CPython's Windows spawn implementation; no PID reopen.
                    duplicate = win32api.DuplicateHandle(proc.sentinel, proof['source_handle'],
                        win32api.GetCurrentProcess(), 0, False, 2)
                    actual = identity(duplicate)
                    if (proof['run_id'] != ctx['run_id'] or proof['worker_pid'] != proc.pid
                        or any(actual[k] != proof[k] for k in actual)
                        or not proof['empty_scenario'] or not proof['absent_from_before']):
                        duplicate.Close()
                        raise RuntimeError('Ownership verification failed; cleanup prohibited')
                    handle = duplicate
                    evidence['ownership'] = dict(proof, supervisor_identity=actual,
                        method='DuplicateHandle from exact live worker HANDLE, identity compared before ACK')
                    write(folder/'ownership.json', evidence['ownership'])
                    parent.send('handle_retained')
                elif msg.get('type') == 'ready_for_timeout':
                    timeout_ready = time.perf_counter()
            elapsed = time.perf_counter() - start
            if handle:
                import win32process
                try:
                    evidence['memory_peak_working_set_bytes'] = max(
                        evidence['memory_peak_working_set_bytes'],
                        win32process.GetProcessMemoryInfo(handle)['PeakWorkingSetSize'])
                except Exception as exc:
                    evidence['memory_query_error'] = repr(exc)
            if elapsed-last_progress >= 15:
                print('PROGRESS', task, round(elapsed, 1), 's', flush=True)
                last_progress = elapsed
            if elapsed > timeout_s or (timeout_ready and time.perf_counter()-timeout_ready > 2):
                evidence['timed_out'] = True
                proc.terminate()
                break
        proc.join(10)
        evidence['worker_exit_code'] = proc.exitcode
    except Exception as exc:
        evidence['supervisor_error'] = repr(exc)
        if proc.is_alive():
            proc.terminate()
            proc.join(10)
    finally:
        if handle:
            try:
                exited = win32event.WaitForSingleObject(handle, 15000) == 0
                if not exited:
                    win32api.TerminateProcess(handle, 125)
                    evidence['forced_termination'] = True
                    exited = win32event.WaitForSingleObject(handle, 10000) == 0
                evidence['residual'] = not exited
                evidence['cleanup'] = ('FORCED_RECOVERY' if evidence['forced_termination'] else 'NORMAL_EXIT') if exited else 'RECOVERY_FAILED'
            except Exception as exc:
                evidence.update(cleanup='RECOVERY_FAILED', residual=True, cleanup_error=repr(exc))
            finally:
                handle.Close()
        else:
            evidence.update(cleanup='BLOCKED_NO_OWNERSHIP', residual='UNKNOWN')
        parent.close()
        if not proc.is_alive():
            proc.close()
        evidence['after'] = snapshot()
        evidence['elapsed_s'] = time.perf_counter() - start
        expected = (task == 'timeout' and evidence['timed_out'] and evidence['forced_termination']) or (
            task != 'timeout' and evidence.get('worker_exit_code') == 0 and not evidence['timed_out']
            and not evidence['forced_termination'])
        evidence['status'] = 'PASS' if (expected and evidence['residual'] is False
            and not evidence.get('supervisor_error')) else 'FAIL'
        write(folder/'lifecycle.json', evidence)
        print('TASK', task, evidence['status'], evidence['cleanup'], round(evidence['elapsed_s'], 3), flush=True)
    return evidence

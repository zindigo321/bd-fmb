"""Capture actual exit; the internal exact-handle supervisor owns STK timeouts."""
import datetime
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT,read,write,files_manifest,utc


def main():
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():raise RuntimeError('fmb required')
    base=(ROOT/'work/round6/latest_preflight.txt').read_text(encoding='utf-8')
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%dT%H%M%S')
    out=Path(r'G:\gpt-file\Codex\outputs\bd-fmb\round6')/(stamp+'_'+uuid.uuid4().hex[:8])
    argv=[sys.executable,'-X','utf8',str(ROOT/'tools/run_round6.py'),'--baseline',base,'--output',str(out)]
    print('OUTPUT',out,flush=True);start=time.perf_counter();started=utc()
    p=subprocess.run(argv,cwd=ROOT,env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1'))
    command=dict(argv=argv,started=True,completed=True,started_utc=started,completed_utc=utc(),exit_code=p.returncode,
        elapsed_s=time.perf_counter()-start,external_timeout=False,outer_process_tree_kill=False)
    write(out/'command_result.json',command)
    if (out/'final_status.json').exists():
        f=read(out/'final_status.json');f['command'].update(command);write(out/'final_status.json',f)
    write(out/'artifact_manifest.json',files_manifest(p for p in out.rglob('*') if p.is_file() and p.name!='artifact_manifest.json'))
    print('COMMAND_EXIT',p.returncode,'ELAPSED',command['elapsed_s'],'OUTPUT',out,flush=True);return p.returncode


if __name__=='__main__':raise SystemExit(main())

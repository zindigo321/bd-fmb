"""Record the actual orchestrator exit without killing its ownership supervisor."""
import datetime
from pathlib import Path
import os
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT, read, write, files_manifest


def main():
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():raise RuntimeError('fmb required')
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%dT%H%M%S')
    output=Path(r'G:\gpt-file\Codex\outputs\bd-fmb\round5')/(stamp+'_'+uuid.uuid4().hex[:8])
    argv=[sys.executable,'-X','utf8',str(ROOT/'tools/run_round5.py'),'--output',str(output)]
    print('OUTPUT',output,flush=True)
    env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['PYTHONDONTWRITEBYTECODE']='1'
    started=time.perf_counter()
    process=subprocess.run(argv,cwd=ROOT,env=env)
    command=dict(argv=argv,started=True,completed=True,exit_code=process.returncode,elapsed_s=time.perf_counter()-started,
        external_timeout=False,supervisor_note='Individual real STK tasks are bounded by the internal exact-handle supervisor; no outer process-tree kill.')
    write(output/'command_result.json',command)
    if (output/'final_status.json').exists():
        f=read(output/'final_status.json');f['command'].update(command);write(output/'final_status.json',f)
    write(output/'artifact_manifest.json',files_manifest(p for p in output.rglob('*') if p.is_file() and p.name!='artifact_manifest.json'))
    print('COMMAND_EXIT',process.returncode,'ELAPSED',command['elapsed_s'],'OUTPUT',output,flush=True)
    return process.returncode


if __name__=='__main__':raise SystemExit(main())

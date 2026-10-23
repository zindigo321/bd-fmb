"""Bounded development probe; not full third-round acceptance."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT, create_run, read, write, summarize, result
from src.round3.lifecycle import supervise

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--tasks',nargs='+',default=['normal','exception','timeout','serial1','serial2','serial3'])
    args=parser.parse_args()
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():
        parser.error('Verified fmb interpreter required')
    cfg=read(ROOT/'configs/round3.json')
    ctx=create_run(args.output,cfg,[ROOT/cfg['input_boundary']],True)
    rows=[]
    for task in args.tasks:
        r=supervise(ctx,cfg,task,Path(ctx['output'])/task,timeout_s=120)
        rows.append(r)
        if r['status']!='PASS':break
    ok=len(rows)==len(args.tasks) and all(r['status']=='PASS' for r in rows)
    write(Path(ctx['output'])/'summary.json',dict(run_id=ctx['run_id'],kind='DEVELOPMENT_TASKS_NOT_FULL_ACCEPTANCE',status='PASS' if ok else 'FAIL',exit_code=0 if ok else 1,tasks=rows))
    return 0 if ok else 1
if __name__=='__main__':raise SystemExit(main())

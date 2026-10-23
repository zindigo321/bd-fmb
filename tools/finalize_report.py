"""Revalidate schema-v2 evidence, render report, and return BUSINESS acceptance status.

Legacy/unbound round-1 directories are rejected without modification.
"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import load_run,read,summarize
from src.round2.report import render
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
    try:
        ctx,_=load_run(args.output);plan=read(Path(args.output)/'stage_plan.json')
        if plan.get('run_id')!=ctx['run_id']:raise ValueError('stage plan does not belong to run')
        summary=summarize(ctx,plan['stages']);render(ctx,summary);return summary['exit_code']
    except (ValueError,OSError,KeyError) as exc:p.error('Refusing unbound or invalid report: '+str(exc))
if __name__=='__main__':raise SystemExit(main())

"""Single acceptance entrypoint. Exit 0 means required checks passed, not just report rendering."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT,create_run,execute,result,validate_stage,read,write,summarize,artifacts
from src.round2.config import load_config

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--config',default=str(ROOT/'configs/round2.json'));p.add_argument('--skip-stk',action='store_true');p.add_argument('--mechanism-test',action='store_true');p.add_argument('--probe-fault',choices=['missing','error']);p.add_argument('--stage-fault',choices=['generate:timeout'])
    args=p.parse_args()
    if Path(sys.prefix).name.lower()!='fmb':p.error('fmb environment required; no base fallback')
    if (args.probe_fault or args.stage_fault) and not args.mechanism_test:p.error('fault injection requires --mechanism-test')
    try:
        cfg=load_config(args.config)
        first=Path(r'G:\gpt-file\Codex\outputs\bd-fmb\smoke_round1_20261023_01')
        # Read-only inputs: original source archive/documents, extracted target materials, and first-round evidence.
        original=read(first/'source_manifest.json')
        paths=[Path(x['path']) if Path(x['path']).is_absolute() else ROOT/x['path'] for x in original]
        paths += list(first.rglob('*'))
        ctx=create_run(args.output,cfg,paths,args.mechanism_test)
    except (ValueError,OSError) as exc:p.error(str(exc))
    stages=[dict(id=x,required=True) for x in ['environment','data','generate']]
    if not args.mechanism_test:stages.append(dict(id='tests',required=True))
    stages.append(dict(id='stk',required=not args.skip_stk))
    write(Path(args.output)/'stage_plan.json',dict(run_id=ctx['run_id'],stages=stages))
    for spec in stages:
        name=spec['id']
        if name=='stk':
            if args.skip_stk:
                result(ctx,name,'SKIPPED',[dict(id='stk_skipped',status='SKIPPED',reason='Explicit --skip-stk; no historical STK files read')]);continue
            dep=[validate_stage(ctx,x) for x in ['environment','generate']]
            probe=read(Path(args.output)/'environment/environment.json')['stk'] if (Path(args.output)/'environment/environment.json').exists() else {}
            if any(x['status']!='PASS' for x in dep) or probe.get('status')!='PASS':
                result(ctx,name,'BLOCKED',[dict(id='stk_dependencies',status='BLOCKED',reason='Current-run environment detection or generation failed',probe=probe)]);continue
        argv=[sys.executable,'-m','src.round2.worker','--output',str(Path(args.output).resolve()),'--stage',name]
        if name=='environment':
            if args.skip_stk:argv.append('--skip-stk')
            if args.probe_fault:argv.extend(['--probe-fault',args.probe_fault])
        timeout=cfg['stk']['timeout_s'] if name=='stk' else 120
        if args.stage_fault=='generate:timeout' and name=='generate':argv=[sys.executable,'-m','src.round2.probe',ctx['output'],'timeout'];timeout=.2
        record=execute(ctx,name,argv,timeout)
        if record['timed_out'] and name=='stk':
            ownership=Path(ctx['output'])/'stk/ownership.json'
            write(Path(ctx['output'])/'stk_timeout_cleanup.json',dict(run_id=ctx['run_id'],status='BLOCKED',reason='Worker timeout; no blind PID termination; possible owned STK residual',ownership=read(ownership) if ownership.exists() else None))
        print(name,record['exit_code'],round(record['elapsed_s'],3),flush=True)
        # Missing/corrupt/timeout evidence is never manufactured into a successful stage.
    summary=summarize(ctx,stages)
    from src.round2.report import render
    render(ctx,summary)
    write(Path(args.output)/'output_manifest.json',dict(run_id=ctx['run_id'],files=artifacts(Path(args.output))))
    print('RUN_ID',ctx['run_id'],'CORE',summary['core_acceptance'],'EXIT',summary['exit_code'],flush=True)
    return summary['exit_code']
if __name__=='__main__':raise SystemExit(main())

"""Third-round serial acceptance. Never uses historical PASS as current evidence."""
import argparse
import datetime
from pathlib import Path
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import (ROOT, KEYS, create_run, execute, read, write, result,
    summarize, artifacts, validate_stage, digest, canonical)
from src.round3.grid import freeze
from src.round3.lifecycle import supervise
from src.constellation import generate


class Tee:
    def __init__(self, stream, path):
        self.stream=stream;self.log=open(path,'w',encoding='utf-8',buffering=1)
    def write(self,text):
        self.log.write(text);return self.stream.write(text)
    def flush(self):
        self.log.flush();self.stream.flush()


def parameter_diff(a,b,path=''):
    rows=[]
    if isinstance(a,dict) and isinstance(b,dict):
        if set(a)!=set(b):return [dict(path=path,status='FAIL',reason='keys differ')]
        for k in a:rows+=parameter_diff(a[k],b[k],path+'.'+k)
    elif isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b):return [dict(path=path,status='FAIL',reason='length differs')]
        for i,(x,y) in enumerate(zip(a,b)):rows+=parameter_diff(x,y,path+f'[{i}]')
    else:
        tolerance=1e-6 if isinstance(a,(float,int)) and not isinstance(a,bool) else 0
        if '_deg' in path:tolerance=1e-5
        if any(s in path for s in ['count','propagator_type','step_s']):tolerance=0
        error=abs(a-b) if isinstance(a,(int,float)) and isinstance(b,(int,float)) else (0 if a==b else None)
        rows.append(dict(path=path,before=a,after=b,tolerance=tolerance,error=error,status='PASS' if error is not None and error<=tolerance else 'FAIL'))
    return rows


def stage_record(ctx,name,started,checks):
    status='PASS' if checks and all(x['status']=='PASS' for x in checks if x.get('required',True)) else 'FAIL'
    result(ctx,name,status,checks)
    write(Path(ctx['output'])/'commands'/f'{name}.json',dict(**{k:ctx[k] for k in KEYS},stage=name,
        argv=[sys.executable,*sys.argv],exit_code=0 if status=='PASS' else 1,timed_out=False,
        elapsed_s=time.perf_counter()-started,execution='in-process supervisor; per-task worker exit codes and logs in lifecycle.json'))
    return status


def run_a(ctx,cfg):
    start=time.perf_counter();folder=Path(ctx['output'])/'A';folder.mkdir();checks=[]
    for task in ['normal','exception','timeout','serial1','serial2','serial3','save','reopen']:
        if time.perf_counter()-start>cfg['round3']['stage_budget_s']['A']:
            checks.append(dict(id='A_budget',status='FAIL',reason='Stage budget exhausted'));break
        payload={'load':str(folder/'save/project/Round3.sc')} if task=='reopen' else {}
        r=supervise(ctx,cfg,task,folder/task,payload,timeout_s=cfg['round3']['task_timeout_s'])
        checks.append(dict(id=task,status=r['status'],evidence=str(folder/task/'lifecycle.json')))
        if r['status']!='PASS':break
    if len(checks)==8 and all(c['status']=='PASS' for c in checks):
        before=read(folder/'save/parameters.json');after=read(folder/'reopen/parameters.json')
        diffs=parameter_diff(before,after)
        write(folder/'parameter_comparison.json',diffs)
        checks.append(dict(id='reopen_parameters',status='PASS' if all(x['status']=='PASS' for x in diffs) else 'FAIL'))
        expected=generate(**cfg['constellation'],constants=cfg['earth'])[0][0]
        targets={'a_km':cfg['earth']['mean_radius_km']+expected['h_km'],'e':0,'i_deg':expected['i_deg'],
                 'arg_perigee_deg':0,'raan_deg':expected['raan_deg'],'true_anomaly_deg':expected['u_deg']}
        expected_ok=all(abs(before['initial_orbit'][k]-v)<(1e-5 if 'deg' in k else 1e-6) for k,v in targets.items())
        expected_ok &= (before['satellite_count']==1 and before['period_epsec']==[0,cfg['duration_s']]
            and before['orbit_epoch_epsec']==0 and before['saved_j2'] and before['saved_frame']=='J2000'
            and before['horizontal_half_deg']==cfg['camera']['along_half_deg']
            and before['vertical_half_deg']==cfg['camera']['cross_half_deg']
            and before['mount_xyzw']==[0,0,0,1] and before['attitude_axis_error']<1e-6
            and all(not p['use_terrain'] and abs(p['geodetic'][2])<1e-6 for p in before['points']))
        checks.append(dict(id='configuration_expected',status='PASS' if expected_ok else 'FAIL',expected_orbit=targets))
        from src.round3.experiments import compare
        a=read(folder/'save/access.json');b=read(folder/'reopen/access.json')
        matches={k:compare(a[k]['intervals'],b[k]['intervals'],[599.95,600.05],cfg['round3']['reopen_tolerance_s']) for k in a}
        write(folder/'access_comparison.json',matches)
        checks.append(dict(id='recomputed_access',status='PASS' if all(x['status']=='PASS' for x in matches.values()) else 'FAIL'))
    return stage_record(ctx,'A',start,checks)


def run_b(ctx,cfg):
    start=time.perf_counter();folder=Path(ctx['output'])/'B';folder.mkdir()
    r=supervise(ctx,cfg,'cases',folder/'cases',timeout_s=cfg['round3']['stage_budget_s']['B'])
    return stage_record(ctx,'B',start,[dict(id='representative_cases',status=r['status'])])


def run_c(ctx,cfg):
    start=time.perf_counter();folder=Path(ctx['output'])/'C';folder.mkdir();checks=[]
    grid=Path(ctx['output'])/'grid/frozen.json'
    for i,duration in enumerate([cfg['round3']['grid']['first_duration_s'],cfg['round3']['grid']['zero_event_second_duration_s']]):
        remaining=cfg['round3']['stage_budget_s']['C']-(time.perf_counter()-start)
        if remaining<60:
            checks.append(dict(id='budget',status='FAIL',reason='Not enough bounded budget for next level'));break
        name='grid'+str(i+1)
        r=supervise(ctx,cfg,name,folder/name,dict(grid=str(grid),duration_s=duration),timeout_s=min(remaining,300))
        checks.append(dict(id=name,status=r['status']))
        if r['status']!='PASS':break
        e=read(folder/name/'experiment.json')
        if e['event_count']>0:break
        if i==0:
            projected=e['elapsed_s']*2+(r['elapsed_s']-e['elapsed_s'])+30
            remaining=cfg['round3']['stage_budget_s']['C']-(time.perf_counter()-start)
            write(folder/'scale_decision.json',dict(run_id=ctx['run_id'],first_task_elapsed_s=r['elapsed_s'],
                first_compute_elapsed_s=e['elapsed_s'],reason='Zero events; predeclared same-point/same-satellite extension to 12000 seconds',
                remaining_budget_s=remaining,projected_second_task_s=projected,point_set_changed=False,
                decision='PROCEED' if projected<min(remaining,300) else 'STOP_BUDGET'))
            if projected>=min(remaining,300):break
    successful=[p for p in folder.glob('grid*/experiment.json') if read(p).get('status')=='PASS']
    if successful:
        latest=successful[-1].parent
        raw=read(latest/'raw_intervals.json');stats=read(latest/'statistics.json')
        sampled=stats['points'][:3]
        positives=[p for p in stats['points'] if p['merged_access_count']]
        if positives:sampled=[positives[0]]+sampled
        checks_manual=[]
        for p in sampled:
            source=[x for x in raw if x['point_id']==p['point_id']]
            # Independent hand-audit ledger: raw sorted endpoints and provenance.
            checks_manual.append(dict(point_id=p['point_id'],raw_sorted=sorted(source,key=lambda x:x['start_epsec']),
                merged=p['merged_intervals'],internal_gaps_s=p['internal_gaps_s'],
                annotation='No raw records: zero visits, no effective gaps, null statistics' if not source else 'Inspect sorted raw endpoints against merged intervals and source IDs'))
        write(folder/'real_point_handcheck.json',checks_manual)
        supported=bool(raw)
        write(folder/'positive_support.json',dict(real_positive_aggregation_supported=supported,
            status='PASS' if supported else 'NOT_DEMONSTRATED_ZERO_EVENTS',
            reason=None if supported else 'Frozen denominator retained; no points moved; synthetic tests are separate'))
        checks.append(dict(id='real_positive_aggregation',status='PASS' if supported else 'BLOCKED',required=True))
    return stage_record(ctx,'C',start,checks)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--config',default=str(ROOT/'configs/round3.json'))
    p.add_argument('--stop-after',choices=['A','B','C'],default='C')
    args=p.parse_args()
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():p.error('Verified fmb interpreter required')
    cfg=read(args.config)
    inputs=[Path(x['path']) for x in read(ROOT/'work/round3_prechange/preservation_manifest.json')]
    original=read(Path(r'G:\gpt-file\Codex\outputs\bd-fmb\smoke_round1_20261023_01')/'source_manifest.json')
    inputs += [Path(x['path']) if Path(x['path']).is_absolute() else ROOT/x['path'] for x in original]
    ctx=create_run(args.output,cfg,inputs)
    out=Path(ctx['output'])
    sys.stdout=Tee(sys.stdout,Path(ctx['work'])/'supervisor.stdout.log')
    sys.stderr=Tee(sys.stderr,Path(ctx['work'])/'supervisor.stderr.log')
    import zipfile
    with zipfile.ZipFile(out/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in read(out/'code_manifest.json')['files']:
            z.write(f['path'],Path(f['path']).relative_to(ROOT))
    specs=[dict(id=x,required=True) for x in ['data','environment','generate','tests','A','B','C']]
    write(out/'stage_plan.json',dict(run_id=ctx['run_id'],stages=specs))
    write(out/'invocation.json',dict(python=sys.executable,argv=sys.argv))
    import shutil
    shutil.copy2(ROOT/'docs/round3_checklist.md',out/'checklist.md')
    shutil.copy2(ROOT/'docs/round3.md',out/'reproduction.md')
    interface_names=['AgUiApplicationLib~IAgUiApplication~Quit.html','STKObjects~IAgStkAccess~RemoveAccess.html',
        'STKObjects~IAgVePropagatorJ2Perturbation.html','STKObjects~Enumerations~AgEClassicalSizeShape_EN.html',
        'STKObjects~IAgStkAccessAdvanced.html','STKObjects~IAgFacility~UseTerrain.html']
    official=[]
    for name in interface_names:
        path=Path(r'G:\Help\Programming\Content\DocX')/name
        official.append(dict(path=str(path),sha256=digest(path.read_bytes())))
    write(out/'official_interfaces.json',dict(sources=official,windows_handle_reference='https://learn.microsoft.com/windows/win32/api/handleapi/nf-handleapi-duplicatehandle'))
    for stage in ['data','environment','generate','tests']:
        r=execute(ctx,stage,[sys.executable,'-m','src.round2.worker','--output',str(out),'--stage',stage],120)
        print('BASE',stage,r['exit_code'],round(r['elapsed_s'],2),flush=True)
    prereq=all(validate_stage(ctx,x,read(out/'commands'/f'{x}.json'))['status']=='PASS' for x in ['data','environment','generate','tests'])
    prereq &= read(out/'environment/environment.json')['stk']['status']=='PASS'
    try:
        freeze(ROOT/cfg['input_boundary'],cfg['round3']['grid'],out/'grid',ctx['run_id'])
        a=run_a(ctx,cfg) if prereq else 'BLOCKED'
        if not prereq:result(ctx,'A','BLOCKED',[dict(id='dependencies',status='BLOCKED')])
        if a=='PASS' and args.stop_after!='A':b=run_b(ctx,cfg)
        else:b='BLOCKED';result(ctx,'B','BLOCKED',[dict(id='dependencies_or_stop',status='BLOCKED')])
        if a=='PASS' and b=='PASS' and args.stop_after=='C':run_c(ctx,cfg)
        else:result(ctx,'C','BLOCKED',[dict(id='dependencies_or_stop',status='BLOCKED')])
    except Exception as exc:
        import traceback
        traceback.print_exc();write(out/'orchestrator_error.json',dict(error=repr(exc)))
        for name in ['A','B','C']:
            if not (out/name/'result.json').exists():result(ctx,name,'FAIL',[dict(id='incomplete',status='FAIL',reason=repr(exc))])
    summary=summarize(ctx,specs)
    write(out/'output_manifest.json',dict(run_id=ctx['run_id'],files=artifacts(out)))
    print('RUN_ID',ctx['run_id'],'ACCEPTANCE',summary['core_acceptance'],'EXIT',summary['exit_code'],flush=True)
    return summary['exit_code']
if __name__=='__main__':raise SystemExit(main())

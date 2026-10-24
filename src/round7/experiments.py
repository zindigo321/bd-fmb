"""Owned-instance round7 diagnostics, paired cold runs, benchmarks and main."""
import copy,importlib.util,time
from pathlib import Path
from src.round2.evidence import ROOT,KEYS,read,write,digest,canonical
from src.round3.experiments import compare
from src.round4.experiments import parameter_check,finish_tier,reopen
from src.round4.frozen import EXPECTED
from src.round6.experiments import diagnostics
from .production import Model,search_batch,ENTRY
from .integrity import integrity


def old_implementation(rootdir):
    rootdir=Path(rootdir);paths=['src/round6/production.py','src/round4/experiments.py','src/round3/experiments.py',
        'src/round3/refine.py','src/round3/statistics.py','src/round4/acceptance.py','src/round2/geometry.py','src/round2/evidence.py']
    closure=[]
    for name in paths:
        old=rootdir/name;current=ROOT/name
        same=digest(old.read_bytes())==digest(current.read_bytes())
        closure.append(dict(relative=name,isolated_path=str(old),sha256=digest(old.read_bytes()),current_identical=same))
        if not same:raise ValueError('Old implementation dependency differs: '+name)
    spec=importlib.util.spec_from_file_location('round7_isolated_old_production',rootdir/'src/round6/production.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    class OldMeasured(mod.Model):
        def __init__(self,*a,**kw):
            self.profile=dict(provider_requests=0,provider_states=0,transform_requests=0,exact_state_requests=0,exact_cache_hits=0,exact_state_s=0.,bulk_basis_write_combined_s=0.)
            super().__init__(*a,**kw)
        def vector(self,*a):
            self.profile['provider_requests']+=1;self.profile['provider_states']+=1;return super().vector(*a)
        def matrix(self,*a):
            self.profile['transform_requests']+=1;return super().matrix(*a)
        def state(self,t):
            self.profile['exact_state_requests']+=1;self.profile['exact_cache_hits']+=int(float(t) in self.cache)
            st=time.perf_counter();r=super().state(t);self.profile['exact_state_s']+=time.perf_counter()-st;return r
        def coarse_states(self,lo,hi,step,phase):
            cold=(lo,hi,step,phase) not in self.coarse_cache;st=time.perf_counter();r=super().coarse_states(lo,hi,step,phase)
            if cold:
                self.profile['provider_requests']+=2;self.profile['provider_states']+=2*len(r[0]);self.profile['bulk_basis_write_combined_s']+=time.perf_counter()-st
            return r
    return OldMeasured,mod.search_batch,closure


def dataset(root,ctx,cfg,payload):
    folder=Path(payload['folder']);stage=payload['stage'];effective=copy.deepcopy(cfg)
    if stage in ['B1','B2']:effective['duration_s']=86400.;window=[0.,86400.];sats=cfg['round7']['benchmarks'][stage]['satellites']
    elif stage in ['cold_old','cold_new']:window=cfg['round7']['cold_pair']['window'];sats=cfg['round7']['cold_pair']['satellites']
    else:window=[0.,604800.];sats=cfg['round7']['satellites']
    write(folder/'effective_config.json',dict(parent_config_sha256=ctx['config_sha256'],config=effective,sha256=digest(canonical(effective)),stage=stage,window=window,satellites=sats))
    selection=read(Path(ctx['output'])/'selection.json');rows=[r for r in selection['baseline'] if r['sat_id'] in sats]
    tier=dict(id=stage,selected=rows);points=read(payload['frozen'])
    if any(points[k]!=ctx[k] for k in KEYS) or digest(canonical(points['points']))!=EXPECTED:raise ValueError('Frozen points identity mismatch')
    cls=Model;search=search_batch;closure=None
    if stage=='cold_old':cls,search,closure=old_implementation(payload['old_root'])
    models=[];parameters={};triples=[];start=time.perf_counter()
    for i,row in enumerate(rows):
        sub=folder/row['sat_id'];sub.mkdir();models.append(cls(root,ctx,effective,sub,row=row,existing=i>0))
    for p in points['points']:
        fac,target,_=models[0].facility(p['point_id'],p['latitude_deg'],p['longitude_deg']);triples.append((p['point_id'],fac,target))
    ids=[p['point_id'] for p in points['points']]
    for model,row in zip(models,rows):parameters[row['sat_id']]=parameter_check(model.readback(ids),row,effective,len(rows),points['points'])
    write(folder/'parameters.json',parameters)
    if any(v['status']!='PASS' for v in parameters.values()):raise ValueError('Parameter readback failed')
    setup=time.perf_counter()-start
    actual=search(models,triples,window,5.,0.,folder,EXPECTED,stage,'MAIN_FINE')
    if stage=='cold_old':
        # Query type is a schema contract, so audit the actual MAIN_FINE records
        # with the old ledger directly and the same explicit trajectory audit.
        from src.round6.integrity import audit_ledger,audit_trajectories
        ledger=audit_ledger(ctx,EXPECTED,actual['opportunities'],actual['candidates'],actual['queries'],actual['raw'],'MAIN_FINE',.05,effective['duration_s'])
        trajectory=audit_trajectories(actual['opportunities'],ctx,folder)
        check=dict(status='PASS' if ledger['status']==trajectory['status']=='PASS' else 'FAIL',ledger=ledger,trajectory=trajectory)
        write(folder/'candidate_query_audit.json',ledger);write(folder/'trajectory_audit.json',trajectory)
    else:check=integrity(ctx,effective,folder)
    t=time.perf_counter()
    result=finish_tier(root,ctx,effective,folder,tier,models,ids,window,actual['opportunities'],actual['raw'],actual['queries'],actual['geometry'],setup,actual['screening_s'],actual['access_and_geometry_s'],start)
    stats=read(folder/'statistics.json');stats['metric_scope']='ROUND7_FULL_28_SMALL_SAMPLE_ENGINEERING_ONLY' if stage=='T7' else 'BENCHMARK_ONLY';write(folder/'statistics.json',stats)
    result.update(kind='FORMAL_28_ORIGINAL16_FULL7D' if stage=='T7' else 'BENCHMARK_ONLY',integrity_status=check['status'],statistics_and_project_write_s=time.perf_counter()-t,
        performance={m.sat_id:m.profile for m in models},cold_cache=True,old_dependency_closure=closure,output_bytes=sum(p.stat().st_size for p in folder.rglob('*') if p.is_file()))
    import win32api,win32process
    result['python_peak_working_set_bytes']=win32process.GetProcessMemoryInfo(win32api.GetCurrentProcess())['PeakWorkingSetSize']
    if check['status']!='PASS':result['status']='FAIL'
    write(folder/'experiment.json',result)
    if result['status']!='PASS':raise ValueError('Dataset evidence integrity failed')
    return result


def known_and_extra(root,ctx,cfg,payload):
    folder=Path(payload['folder']);prior=Path(cfg['round7']['source_round6']);old=read(prior/'G/T6/raw_intervals.json')
    selection=read(Path(ctx['output'])/'selection.json');rows={r['sat_id']:r for r in selection['baseline']}
    points=read(Path(ctx['output'])/'grid/frozen.json')['points'];byid={p['point_id']:p for p in points};models={};facilities={};results=[]
    def get(sat):
        if sat not in models:
            sub=folder/sat;sub.mkdir();models[sat]=Model(root,ctx,cfg,sub,row=rows[sat],existing=bool(models))
        return models[sat]
    def perform(name,m,pid,fac,target,window,reference,phash):
        # Reference is computed over the predeclared whole local window and
        # does not depend on proposed candidates.
        native=m.access(fac,window,cfg['access_tiers'][-1]);native['query_type']='INDEPENDENT_NATIVE_REFERENCE'
        sub=folder/name;a=search_batch([m],[(pid,fac,target)],window,5.,0.,sub,phash,name,'DIAGNOSTIC_FINE')
        chain=integrity(ctx,cfg,sub,phash,'DIAGNOSTIC_FINE',folder)
        cmp=compare(native['intervals'],a['raw'],window,cfg['tolerances']['convergence_s'])
        ref=compare(reference,a['raw'],window,cfg['tolerances']['independent_event_s'])
        ok=cmp['status']==ref['status']==chain['status']=='PASS' and all(g['status']=='PASS' for g in a['geometry'])
        results.append(dict(case=name,status='PASS' if ok else 'FAIL',native_reference=native,native_comparison=cmp,angular_or_historical_comparison=ref,
            folder=str(sub),events=len(a['raw']),entry=ENTRY))
        write(folder/'results.json',results)
    for i,e in enumerate(old):
        m=get(e['sat_id']);pid=e['point_id'];p=byid[pid]
        if pid not in facilities:facilities[pid]=m.facility(pid,p['latitude_deg'],p['longitude_deg'])[:2]
        fac,target=facilities[pid];center=(e['start_epsec']+e['stop_epsec'])/2
        perform(f'known_{i:02d}',m,pid,fac,target,[center-30,center+30],[e],EXPECTED)
    for i,(seed,cross,along) in enumerate([(900.337,.99999,.8),(2600.419,-.99999,-.8)]):
        m=get('Sat001');pid=f'X_corner_{i}';fac,target,info=m.diagnostic(pid,seed,cross,along);ref=m.reference(target,seed)
        if len(ref['intervals'])!=1:raise ValueError('Predeclared corner fixture must have one angular event')
        write(folder/(pid+'_reference.json'),dict(info=info,angular=ref))
        perform(pid,m,pid,fac,target,[seed-30,seed+30],ref['intervals'],digest(canonical(info)))
    result=dict(status='PASS' if len(results)==14 and all(x['status']=='PASS' for x in results) else 'FAIL',known12=len(results[:12]),extra2=len(results[12:]),history_seeds_in_main=False)
    write(folder/'experiment.json',result)
    if result['status']!='PASS':raise ValueError('Independent known/extra short windows failed')
    return result


def dispatch(root,ctx,cfg,task,payload):
    if task=='r7_diagnostics':return diagnostics(root,ctx,cfg,payload,Model,search_batch,integrity,ENTRY,native_reference=True)
    if task=='r7_known_extra':return known_and_extra(root,ctx,cfg,payload)
    if task.startswith('r7_dataset_'):return dataset(root,ctx,cfg,payload)
    if task=='r7_reopen':
        result=reopen(root,ctx,cfg,payload)
        path=Path(payload['folder'])/'access_comparison.json';rows=read(path)
        for row in rows:row['query'].update(query_type='REOPEN_NEGATIVE' if row.get('negative') else 'REOPEN_POSITIVE',query_status='COMPLETE')
        write(path,rows);return result
    raise ValueError('Unknown round7 task')

"""Real diagnostic and formal work in separate exact-handle-owned instances."""
import math
from pathlib import Path
from src.constellation import generate
from src.round2.evidence import KEYS,read,write,canonical,digest
from src.round3.experiments import compare
from src.round4.experiments import run_tier,reopen
from .production import Model,search_batch,ENTRY
from .audit import integrity


def diagnostics(root,ctx,cfg,payload,Model=Model,search_batch=search_batch,integrity=integrity,ENTRY=ENTRY,native_reference=False):
    folder=Path(payload['folder']);model_dir=folder/'Sat001';model_dir.mkdir()
    model=Model(root,ctx,cfg,model_dir)
    specs=[('r2_center',600,0,None),('low_ascending',120.137,0,None),('mid_ascending',900.271,0,None),
        ('high_edge',1400.413,.8,None),('high_descending',1800.617,-.8,None),('low_descending',2600.731,0,None),
        ('south_descending',3100.193,0,None),('south_edge',3900.347,.98,None),('south_ascending',4700.529,0,None),
        ('late_phase',5400.863,-.98,None),('negative_north',900.337,1.5,None),('negative_descending',2600.419,-1.5,None),
        ('short_north',1800.351,.999999,None),('short_south',3900.717,-.999999,None),
        ('start_clipped',600.183,0,'start'),('end_clipped',2600.257,0,'end')]
    results=[];references=[];constructed=[]
    def perform(m,pid,fac,target,window,step,phase,name,variant,reference,point_hash):
        sub=folder/name/str(variant)
        actual=search_batch([m],[(pid,fac,target)],window,step,phase,sub,point_hash,f'{name}_{variant}','DIAGNOSTIC_FINE')
        match=compare(reference,actual['raw'],window,cfg['tolerances']['independent_event_s'])
        audit=integrity(ctx,cfg,sub,point_hash,'DIAGNOSTIC_FINE',folder)
        geometry_ok=all(g['status']=='PASS' for g in actual['geometry'])
        native=None
        if native_reference:
            # Declared whole diagnostic window, independent of returned candidates.
            native=m.access(fac,window,cfg['access_tiers'][-1])
            native['query_type']='INDEPENDENT_NATIVE_REFERENCE'
            native['comparison']=compare(native['intervals'],actual['raw'],window,cfg['tolerances']['convergence_s'])
            geometry_ok &= native['comparison']['status']=='PASS'
        result=dict(case_id=name,variant=variant,entry=ENTRY,window=window,step_s=step,phase_s=phase,
            comparison=match,native_reference=native,integrity_status=audit['status'],geometry_pass=geometry_ok,event_count=len(actual['raw']),
            refinement_candidates=len(actual['candidates']),intervals=actual['raw'],folder=str(sub),
            status='PASS' if match['status']==audit['status']=='PASS' and geometry_ok else 'FAIL')
        results.append(result);write(folder/'search_results.json',results)
        return result
    for name,t,cross,clip in specs:
        pid='D_'+name;fac,target,info=model.diagnostic(pid,t,cross);ref=model.reference(target,t)
        if len(ref['intervals'])!=(0 if abs(cross)>1 else 1):raise ValueError('Construction reference expectation failed')
        constructed.append(info);references.append(dict(case_id=name,**ref))
        base=math.floor(t/60)*60-10
        variants=[(base,base+120,5,0),(base-3.713,base+133.431,2,.371),(base+1.219,base+124.537,1,.619)]
        if clip=='start':variants=[(t+.001,t+60+s,st,ph) for s,st,ph in [(0,5,0),(13.431,2,.371),(4.537,1,.619)]]
        if clip=='end':variants=[(t-60-s,t-.001,st,ph) for s,st,ph in [(0,5,0),(13.431,2,.371),(4.537,1,.619)]]
        for v,(lo,hi,step,phase) in enumerate(variants):perform(model,pid,fac,target,[lo,hi],step,phase,name,v,ref['intervals'],digest(canonical(info)))
        # Same clipped physical event must remain stable across all three grids.
        current=results[-3:]
        for result in current[1:]:
            result['alignment']=compare(current[0]['intervals'],result['intervals'],result['window'],cfg['tolerances']['convergence_s'])
            if result['alignment']['status']!='PASS':result['status']='FAIL'
        write(folder/'search_results.json',results);write(folder/'references.json',references);write(folder/'cases.json',constructed)
        print('CASE',name,[x['status'] for x in current],flush=True)
    # Historical times define diagnostic windows only; the shared search never sees a seed.
    prior=Path(cfg['round6']['source_round5']);oldqueries=read(prior/'G/T5/raw_access_queries.json')
    points=read(prior/'grid/frozen.json');byid={p['point_id']:p for p in points['points']}
    rows={r['sat_id']:r for r in generate(**cfg['constellation'],constants=cfg['earth'])[0]}
    models={'Sat001':model};facilities={};mapping=[]
    for old in oldqueries:
        sat=old['sat_id'];pid=old['point_id']
        if sat not in models:
            sub=folder/sat;sub.mkdir();models[sat]=Model(root,ctx,cfg,sub,row=rows[sat],existing=True)
        m=models[sat]
        if pid not in facilities:
            p=byid[pid];facilities[pid]=m.facility(pid,p['latitude_deg'],p['longitude_deg'])[:2]
        fac,target=facilities[pid];center=sum(old['window'])/2;name='history_'+old['query_id'];local=[]
        for v,(step,phase) in enumerate([(5.,0.),(2.,.371),(1.,.619)]):
            local.append(perform(m,pid,fac,target,[center-60,center+60],step,phase,name,v,old['intervals'],points['point_set_sha256']))
        mapping.append(dict(source_query_id=old['query_id'],source_run_id=cfg['round6']['source_round5_run_id'],
            source_event_count=old['event_count'],cases=[dict(case_id=x['case_id'],variant=x['variant'],status=x['status'],
                refinement_candidates=x['refinement_candidates'],event_count=x['event_count']) for x in local]))
    write(folder/'history_coverage_mapping.json',mapping)
    ok=len(results)==60 and all(x['status']=='PASS' for x in results) and all(x['refinement_candidates']>=1 for x in results[48:])
    result=dict(status='PASS' if ok else 'FAIL',cases=16,searches=48,history_searches=12,total_searches=len(results),
        entry=ENTRY,kind='DIAGNOSTIC_ONLY',all_geometry_and_integrity_checked=True,history_used_in_main=False)
    write(folder/'experiment.json',result)
    if not ok:raise ValueError('Production diagnostics prerequisite failed; main experiment prohibited')
    return result


def dispatch(root,ctx,cfg,task,payload):
    if task=='r6_diagnostics':return diagnostics(root,ctx,cfg,payload)
    if task=='r6_main':return run_tier(root,ctx,cfg,payload,production_search=search_batch,model_class=Model)
    if task=='r6_reopen':
        result=reopen(root,ctx,cfg,payload)
        path=Path(payload['folder'])/'access_comparison.json';rows=read(path)
        for row in rows:row['query'].update(query_type='REOPEN_NEGATIVE' if row.get('negative') else 'REOPEN_POSITIVE',query_status='COMPLETE')
        write(path,rows);return result
    raise ValueError('Unknown round6 task')

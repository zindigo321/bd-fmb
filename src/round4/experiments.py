"""Frozen-grid experiments inside the existing exact-handle STK supervisor."""
import csv
import math
from pathlib import Path
import time
import numpy as np
from src.round2.evidence import KEYS,canonical,digest,read,write
from src.round2.geometry import angles,geodetic_to_ecef
from src.round3.experiments import Model,csv_out,compare
from src.round3.refine import root as refine_root
from src.round3.statistics import aggregate
from .acceptance import manual_audit,evaluate
from .frozen import EXPECTED


def screen(model,target,window,phase=0.):
    started=time.perf_counter();step=model.cfg['round4']['coarse_step_s']
    times,pos,bases,trajectory=model.coarse_states(*window,step,phase)
    pos=np.asarray(pos);bases=np.asarray(bases);delta=target-pos
    body=np.einsum('nji,nj->ni',bases,delta)
    normal=np.array([target[0]/model.a**2,target[1]/model.a**2,target[2]/model.b**2]);normal/=np.linalg.norm(normal)
    elevation=np.degrees(np.arcsin(np.clip((-delta@normal)/np.linalg.norm(delta,axis=1),-1,1)))
    closest=int(np.argmax(elevation));ranges=np.linalg.norm(delta,axis=1);minimum_range=int(np.argmin(ranges))
    coarse=[];candidates=[]
    for i in np.flatnonzero((body[:-1,0]*body[1:,0]<=0)&(np.minimum(body[:-1,2],body[1:,2])>0)):
        a,b=times[i],times[i+1]
        item=dict(coarse_bracket=[a,b],sample_max_elevation_deg=float(max(elevation[i],elevation[i+1])),
            coarse_cross_angle_deg=float(np.degrees(np.arctan2(body[i,1],body[i,2]))),exact_root_epsec=None)
        if item['sample_max_elevation_deg'] < -model.cfg['round4']['horizon_guard_deg']:
            item['reason_code']='SAMPLED_BELOW_HORIZON_WITH_GUARD';coarse.append(item);continue
        left=max(window[0],a-step);right=min(window[1],b+step)
        def along(t):
            p,axes=model.state(t);return float((axes.T@(target-p))[0])
        if along(left)*along(right)>0:raise ValueError('Candidate not bracketed in exact state; no silent rejection')
        t=refine_root(along,left,right,model.cfg['round3']['search']['root_tolerance_s'])
        if candidates and abs(t-candidates[-1]['exact_root_epsec'])<.01:continue
        p,axes=model.state(t);x,y,z=angles(p,axes,target)
        el=math.degrees(math.asin(float(np.clip(np.dot(p-target,normal)/np.linalg.norm(p-target),-1,1))))
        item.update(exact_root_epsec=t,exact_along_deg=x,exact_cross_deg=y,exact_elevation_deg=el,
            cross_margin_deg=model.cam['cross_half_deg']-abs(y),cross_gate_deg=2*model.cam['cross_half_deg'])
        if el<0:item['reason_code']='EXACT_CENTER_BELOW_HORIZON'
        elif z<=0 or abs(y)>2*model.cam['cross_half_deg']:item['reason_code']='VISIBLE_CENTER_OUTSIDE_CROSS_GATE'
        else:item['reason_code']='REFINE_SENSOR_ACCESS'
        candidates.append(item);coarse.append(item)
    return dict(window=list(window),coarse_step_s=step,phase_s=phase,coarse_samples=len(times),
        max_sampled_elevation_deg=float(elevation[closest]),max_elevation_time_epsec=times[closest],
        closest_range_km=float(ranges[minimum_range]),closest_range_time_epsec=times[minimum_range],
        visible_samples=int(sum(elevation>=0)),coarse_crossings=len(coarse),exact_candidates=len(candidates),
        refinement_candidates=sum(x['reason_code']=='REFINE_SENSOR_ACCESS' for x in candidates),
        candidates=coarse,trajectory=trajectory,screen_elapsed_s=time.perf_counter()-started,
        scope='Finite 5s screening plus exact candidate roots; sampled absence is not continuous absence. Tangencies, multiple zero crossings within a cell, and edge-only overlap without center crossing are not proved complete.')


def parameter_check(rb,row,cfg,count):
    o=rb['initial_orbit'];expected=dict(a_km=cfg['earth']['mean_radius_km']+row['h_km'],e=0,i_deg=row['i_deg'],arg_perigee_deg=0,raan_deg=row['raan_deg'],true_anomaly_deg=row['u_deg'])
    checks={k:abs(o[k]-v)<=1e-9 for k,v in expected.items()}
    checks.update(count=rb['satellite_count']==count,epoch=rb['epoch_utcg']==cfg['epoch_utcg'],orbit_epoch=abs(rb['orbit_epoch_epsec'])<1e-9,
        period=rb['period_epsec']==[0.,cfg['duration_s']],j2=rb['propagator_type']==1,step=rb['step_s']==cfg['propagation_step_s'],
        attitude=rb['attitude_axis_error']<=cfg['tolerances']['axis_vector'],
        along=abs(rb['horizontal_half_deg']-cfg['camera']['along_half_deg'])<1e-12,
        cross=abs(rb['vertical_half_deg']-cfg['camera']['cross_half_deg'])<1e-12,
        mount=rb['mount_xyzw']==cfg['camera']['mount_quaternion_xyzw'],terrain=all(not p['use_terrain'] and abs(p['geodetic'][2])<1e-6 for p in rb['points']))
    return dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,expected_orbit=expected,readback=rb)


def audit_geometry(model,fac,target,record):
    center=(record['start_epsec']+record['stop_epsec'])/2
    ref=model.reference(target,center)
    cmp=compare(ref['intervals'],[record],record['window'],model.cfg['tolerances']['independent_event_s'])
    # New objects/cache invalidation for an independently configured Access convergence check.
    window=[max(record['window'][0],center-.05),min(record['window'][1],center+.05)]
    medium=model.access(fac,window,model.cfg['access_tiers'][1])
    convergence=compare([record],medium['intervals'],window,model.cfg['tolerances']['convergence_s'])
    return dict(record_id=record['record_id'],status='PASS' if cmp['status']==convergence['status']=='PASS' else 'FAIL',
        angular_reference=ref,boundary_comparison=cmp,medium_access=medium,convergence=convergence,
        independence='Angular geometry uses the same STK propagated trajectory and coordinate transform, but no Access to derive angular boundaries. It verifies observed events, not global search completeness.')


def run_tier(root,ctx,cfg,payload):
    start=time.perf_counter();folder=Path(payload['folder']);tier=payload['tier'];points=read(payload['frozen'])
    if any(points[k]!=ctx[k] for k in KEYS) or digest(canonical(points['points']))!=EXPECTED:raise ValueError('Current frozen wrapper identity mismatch')
    ids=[p['point_id'] for p in points['points']];window=[0.,cfg['duration_s']];models=[];facilities={};targets={};parameters={}
    for i,row in enumerate(tier['selected']):
        sub=folder/row['sat_id'];sub.mkdir()
        models.append(Model(root,ctx,cfg,sub,row=row,existing=i>0))
    for p in points['points']:
        fac,target,rb=models[0].facility(p['point_id'],p['latitude_deg'],p['longitude_deg'])
        facilities[p['point_id']]=fac;targets[p['point_id']]=target
    for model,row in zip(models,tier['selected']):
        parameters[row['sat_id']]=parameter_check(model.readback(ids),row,cfg,len(models))
    write(folder/'parameters.json',parameters)
    if any(p['status']!='PASS' for p in parameters.values()):raise ValueError('Baseline parameter readback failed')
    propagation_elapsed=time.perf_counter()-start
    # Complete every pair's opportunity screen before any fine Access call.
    opportunities=[];screen_start=time.perf_counter()
    for model in models:
        for index,pid in enumerate(ids):
            item=screen(model,targets[pid],window);item.update(sat_id=model.sat_id,point_id=pid)
            opportunities.append(item)
            print('SCREEN',tier['id'],model.sat_id,index+1,len(ids),item['max_sampled_elevation_deg'],item['refinement_candidates'],flush=True)
        write(folder/'opportunities_screened.json',dict(run_id=ctx['run_id'],complete=len(opportunities)==len(models)*len(ids),pairs=opportunities))
    screening_elapsed=time.perf_counter()-screen_start
    write(folder/'screen_complete_before_access.json',dict(run_id=ctx['run_id'],pairs=len(opportunities),access_calls=sum(m.access_calls for m in models),elapsed_s=screening_elapsed))
    raw=[];queries=[];geometry=[];lookup={m.sat_id:m for m in models};fine_start=time.perf_counter()
    for item in opportunities:
        model=lookup[item['sat_id']];pid=item['point_id'];item['event_count']=0;item['queries']=[]
        for candidate in item['candidates']:
            if candidate['reason_code']!='REFINE_SENSOR_ACCESS':continue
            t=candidate['exact_root_epsec'];w=[max(window[0],t-.05),min(window[1],t+.05)]
            q=model.access(facilities[pid],w);qid=f'{tier["id"]}_Q{len(queries):04d}'
            q.update(query_id=qid,point_id=pid,sat_id=model.sat_id,sensor_id='Camera',run_id=ctx['run_id'],evaluation_window=window,
                config_sha256=ctx['config_sha256'],point_set_sha256=EXPECTED,tier_id=tier['id'])
            queries.append(q);item['queries'].append(qid);candidate.update(query_id=qid,refinement_window=w,access_events=q['event_count'])
            for event in q['intervals']:
                r=dict(event,record_id=f'{ctx["run_id"]}_{tier["id"]}_{len(raw):05d}',point_id=pid,sat_id=model.sat_id,sensor_id='Camera',
                    run_id=ctx['run_id'],config_sha256=ctx['config_sha256'],point_set_sha256=EXPECTED,tier_id=tier['id'],window=window,
                    local_refinement_window=w,event_status='REAL_SENSOR_ACCESS',query_id=qid)
                raw.append(r);item['event_count']+=1
                geometry.append(audit_geometry(model,facilities[pid],targets[pid],r))
        item['outcome_code']=('REAL_IMAGING_EVENT' if item['event_count'] else 'CANDIDATES_REFINED_NO_EVENT' if item['queries'] else
            'VISIBLE_BUT_CROSS_FIELD_UNSATISFIED' if item['visible_samples'] else 'NO_SAMPLED_HORIZON_OPPORTUNITY')
    fine_elapsed=time.perf_counter()-fine_start
    for model in models:write(model.folder/'raw_intervals.json',[r for r in raw if r['sat_id']==model.sat_id])
    write(folder/'raw_access_queries.json',queries);write(folder/'raw_intervals.json',raw);write(folder/'geometry_audit.json',geometry)
    fields=['record_id','run_id','tier_id','point_id','sat_id','sensor_id','start_epsec','stop_epsec','duration_s','event_status','query_id','config_sha256','point_set_sha256','window_start_epsec','window_stop_epsec']
    csvrows=[dict({k:r[k] for k in fields if k in r},window_start_epsec=window[0],window_stop_epsec=window[1]) for r in raw]
    csv_out(folder/'raw_intervals.csv',csvrows,fields)
    with (folder/'raw_intervals.csv').open(encoding='utf-8',newline='') as f:back=list(csv.DictReader(f))
    consistency=len(back)==len(raw) and all(all(str(r[k])==b[k] for k in fields if k in r) for r,b in zip(raw,back))
    query_events=[(q['query_id'],e['start_epsec'],e['stop_epsec']) for q in queries for e in q['intervals']]
    consistency &= query_events==[(r['query_id'],r['start_epsec'],r['stop_epsec']) for r in raw]
    stats=aggregate(ids,raw,window,merge_tolerance_s=0);stats['metric_scope']='ROUND4_SMALL_SAMPLE_ENGINEERING_ONLY'
    write(folder/'statistics.json',stats)
    write(folder/'merged_intervals.json',[dict(point_id=p['point_id'],intervals=p['merged_intervals']) for p in stats['points']])
    write(folder/'provenance.json',[dict(point_id=p['point_id'],merged_index=i,sources=[r for r in raw if r['record_id'] in m['source_record_ids']]) for p in stats['points'] for i,m in enumerate(p['merged_intervals'])])
    hand=manual_audit(ids,raw,stats,window);write(folder/'real_point_handcheck.json',hand)
    accept=evaluate(raw,stats,hand,geometry,consistency)
    accept['per_satellite_event_counts']={m.sat_id:sum(r['sat_id']==m.sat_id for r in raw) for m in models}
    write(folder/'acceptance.json',accept);write(folder/'opportunities.json',opportunities)
    fields=['sat_id','point_id','max_sampled_elevation_deg','max_elevation_time_epsec','closest_range_km','closest_range_time_epsec','visible_samples','coarse_crossings','exact_candidates','refinement_candidates','event_count','outcome_code']
    csv_out(folder/'opportunities.csv',[{k:p[k] for k in fields} for p in opportunities],fields)
    project=folder/'project';project.mkdir();root.SaveScenarioAs(str(project/'Round4.sc'))
    summary=dict(status='PASS' if consistency and hand['status']=='PASS' and all(g['status']=='PASS' for g in geometry) else 'FAIL',
        kind='REAL_FIXED_GRID',tier_id=tier['id'],window=window,points=len(ids),satellites=len(models),event_count=len(raw),
        propagation_setup_s=propagation_elapsed,screening_s=screening_elapsed,access_and_geometry_s=fine_elapsed,
        elapsed_s=time.perf_counter()-start,state_provider_calls=sum(m.calls for m in models),access_calls=sum(m.access_calls for m in models),
        acceptance=accept,json_csv_query_consistency=bool(consistency),project=str(project/'Round4.sc'))
    write(folder/'experiment.json',summary)
    if summary['status']!='PASS':raise ValueError('Tier validation failed')
    return summary


def reopen(root,ctx,cfg,payload):
    source=Path(payload['source']);folder=Path(payload['folder']);tier=payload['tier'];old=read(source/'parameters.json')
    raw=read(source/'raw_intervals.json');ids=[p['point_id'] for p in read(payload['frozen'])['points']];comparisons=[];queries=[]
    for i,row in enumerate(tier['selected']):
        sub=folder/row['sat_id'];sub.mkdir()
        model=Model(root,ctx,cfg,sub,load=source/'project/Round4.sc',row=row,existing=i>0)
        rb=parameter_check(model.readback(ids),row,cfg,len(tier['selected']))
        def diff(a,b,path=''):
            if isinstance(a,dict):return sum((diff(a[k],b[k],path+'/'+k) for k in a),[])
            if isinstance(a,list):return sum((diff(x,y,path+'/'+str(j)) for j,(x,y) in enumerate(zip(a,b))),[]) if len(a)==len(b) else [path]
            if isinstance(a,(int,float)) and not isinstance(a,bool):return [] if abs(a-b)<=1e-9 else [path]
            return [] if a==b else [path]
        errors=diff(old[row['sat_id']]['readback'],rb['readback']);comparisons.append(dict(sat_id=row['sat_id'],parameter_check=rb,differences=errors))
        for r in [x for x in raw if x['sat_id']==row['sat_id']]:
            q=model.access(root.GetObjectFromPath('Facility/'+r['point_id']),r['local_refinement_window'])
            cmp=compare([r],q['intervals'],r['local_refinement_window'],cfg['tolerances']['convergence_s'])
            queries.append(dict(record_id=r['record_id'],query=q,comparison=cmp))
        # Independently recomputed negative query from this same frozen grid.
        q=model.access(root.GetObjectFromPath('Facility/'+ids[0]),[0.,.1]);queries.append(dict(negative=True,query=q,comparison=dict(status='PASS' if not q['intervals'] else 'FAIL')))
    write(folder/'parameter_comparison.json',comparisons);write(folder/'access_comparison.json',queries)
    ok=all(x['parameter_check']['status']=='PASS' and not x['differences'] for x in comparisons) and all(x['comparison']['status']=='PASS' for x in queries)
    obj=dict(status='PASS' if ok else 'FAIL',positive_events_recomputed=len(raw),negative_queries=len(tier['selected']),source_project=str(source/'project/Round4.sc'))
    write(folder/'experiment.json',obj)
    if not ok:raise ValueError('Round4 reopen verification failed')
    return obj


def dispatch(root,ctx,cfg,task,payload):
    if task=='r4_reopen':return reopen(root,ctx,cfg,payload)
    if task.startswith('r4_T'):return run_tier(root,ctx,cfg,payload)
    raise ValueError('Unknown round4 experiment')

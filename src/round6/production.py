"""The only round6 screening -> candidate -> Sensor Access -> event entry."""
from pathlib import Path
import time
import numpy as np
from src.round2.evidence import KEYS,read,write,digest
from src.round2.geometry import body_basis
from src.round3.experiments import Model as BaseModel
from src.round4.experiments import screen,audit_geometry

ENTRY='src.round6.production.search_batch'


class Model(BaseModel):
    def coarse_states(self,lo,hi,step,phase):
        key=(lo,hi,step,phase)
        if key in self.coarse_cache:return self.coarse_cache[key]
        # STK11 ExecElements rounds its interior cadence to whole seconds.
        # Explicit time arrays make the declared phase an actual state request.
        interior=np.arange(lo+phase,hi,step,dtype=float)
        requested=np.concatenate(([lo],interior[(interior>lo+1e-8)&(interior<hi-1e-8)],[hi]))
        blocks=[]
        for provider in ['Cartesian Position','Cartesian Velocity']:
            data=self.sat.DataProviders.Item(provider).Group.Item('Fixed').ExecSingleElementsArray(
                requested.tolist(),['Time','x','y','z'])
            self.calls+=1
            if not data.Valid or data.Count!=4:raise ValueError('Explicit trajectory provider result invalid')
            blocks.append({k:np.asarray(data.GetArray(k),dtype=float) for k in ['Time','x','y','z']})
        times=blocks[0]['Time']
        if any(b['Time'].shape!=requested.shape or not np.allclose(b['Time'],requested,rtol=0,atol=1e-8) for b in blocks):
            raise ValueError('Provider did not honor explicit trajectory time array')
        if not np.array_equal(times,blocks[1]['Time']) or not np.all(np.diff(times)>0):raise ValueError('Explicit provider time mismatch/order')
        positions=np.column_stack([blocks[0][k] for k in 'xyz']);velocities=np.column_stack([blocks[1][k] for k in 'xyz'])
        if positions.shape!=(len(times),3) or velocities.shape!=(len(times),3) or not np.all(np.isfinite(positions)) or not np.all(np.isfinite(velocities)):
            raise ValueError('Explicit trajectory state dimensions/nonfinite values')
        omega=np.array([0.,0.,self.cfg['round3']['search']['coarse_earth_rotation_rad_s']])
        bases=[body_basis(p,v+np.cross(omega,p)) for p,v in zip(positions,velocities)]
        calibration=[]
        for index in [0,len(times)-1]:
            ep,eb=self.state(times[index]);calibration.append(dict(time_epsec=float(times[index]),
                position_error_km=float(np.linalg.norm(positions[index]-ep)),axis_error=float(np.max(np.linalg.norm(bases[index]-eb,axis=0)))))
        if max(c['axis_error'] for c in calibration)>self.cfg['round3']['search']['coarse_axis_tolerance']:
            raise ValueError('Coarse rotation approximation exceeds frozen tolerance')
        path=self.folder/'coarse_trajectory'/f'{len(self.coarse_cache):03d}.json'
        evidence=dict(**{k:self.ctx[k] for k in KEYS},sat_id=self.sat_id,window=[lo,hi],step_s=step,phase_s=phase,
            calibration=calibration,times_epsec=times.tolist(),requested_times_epsec=requested.tolist(),positions_fixed_km=positions.tolist(),
            bases_screening=[b.tolist() for b in bases],method='STK ExecSingleElementsArray at explicit times; Fixed position/velocity plus frozen omega cross r screening basis',
            sampling_rule='screening_lo + phase + k*step, with both window endpoints; endpoint tolerance 1e-8 s')
        write(path,evidence)
        result=(times.tolist(),positions,bases,dict(path=str(path),sha256=digest(path.read_bytes()),calibration=calibration))
        self.coarse_cache[key]=result
        return result


def search_batch(models,points,window,step,phase,folder,point_hash,prefix,kind):
    """No event seeds or reference intervals are accepted by this interface.

    Fixed screening padding is inherited from the frozen round3 setting.
    Native queries retain their local, uncut results; the evaluated query
    intervals and emitted events are clipped explicitly to the evaluation window.
    """
    started=time.perf_counter();folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    ctx=models[0].ctx;cfg=models[0].cfg;search=cfg['round3']['search'];pad=search['padding_s']
    identity={**{k:ctx[k] for k in KEYS},'point_set_sha256':point_hash,'sensor_id':'Camera'}
    screening=[max(0.,window[0]-pad),min(cfg['duration_s'],window[1]+pad)]
    opportunities=[];candidates=[];queries=[];raw=[];geometry=[]
    lookup={m.sat_id:m for m in models};point_lookup={p[0]:p[1:] for p in points}
    access_before=sum(m.access_calls for m in models)
    for model in models:
        for pi,(pid,facility,target) in enumerate(points):
            o=screen(model,target,screening,phase=phase,step=step,retain_sources=True)
            o.update(identity,sat_id=model.sat_id,point_id=pid,window=list(window),evaluation_window=list(window),
                screening_window=screening,padding_s=pad,entry=ENTRY,queries=[],event_count=0)
            pair=f'{prefix}_{model.sat_id}_{pid}'
            for i,c in enumerate(o['candidates']):
                c['source_id']=f'{pair}_S{i:04d}'
                if c['reason_code']=='REFINE_SENSOR_ACCESS':
                    c['candidate_id']=f'{pair}_C{i:04d}'
                    t=c['exact_root_epsec'];local=[max(0.,t-search['candidate_half_window_s']),min(cfg['duration_s'],t+search['candidate_half_window_s'])]
                    candidates.append(dict(identity,candidate_id=c['candidate_id'],sat_id=model.sat_id,point_id=pid,
                        candidate_root_epsec=t,coarse_bracket=c['coarse_bracket'],evaluation_window=list(window),
                        screening_window=screening,local_query_window=local,source_ids=[c['source_id']],query_status='PENDING'))
            for c in o['candidates']:
                if c['reason_code']=='DUPLICATE_EXACT_ROOT':
                    retained=o['candidates'][c['retained_coarse_index']]
                    if retained['reason_code']=='REFINE_SENSOR_ACCESS':
                        next(x for x in candidates if x['candidate_id']==retained['candidate_id'])['source_ids'].append(c['source_id'])
            opportunities.append(o)
            print('SCREEN',prefix,model.sat_id,pi+1,len(points),o['refinement_candidates'],flush=True)
        write(folder/'opportunities_screened.json',dict(run_id=ctx['run_id'],complete=len(opportunities)==len(models)*len(points),pairs=opportunities))
    screening_s=time.perf_counter()-started
    write(folder/'screen_complete_before_access.json',dict(run_id=ctx['run_id'],pairs=len(opportunities),
        access_calls=sum(m.access_calls for m in models)-access_before,elapsed_s=screening_s,entry=ENTRY))
    write(folder/'candidate_ledger.json',candidates)
    fine_start=time.perf_counter();cmap={c['candidate_id']:c for c in candidates}
    for o in opportunities:
        model=lookup[o['sat_id']];facility,target=point_lookup[o['point_id']]
        for source in o['candidates']:
            if source['reason_code']!='REFINE_SENSOR_ACCESS':continue
            c=cmap[source['candidate_id']];qid=f'{prefix}_Q{len(queries):05d}';c['query_id']=qid;source['query_id']=qid
            q=dict(identity,query_id=qid,candidate_id=c['candidate_id'],sat_id=model.sat_id,point_id=o['point_id'],
                evaluation_window=list(window),window=c['local_query_window'],query_type=kind,query_status='STARTED',
                candidate_root_epsec=c['candidate_root_epsec'],parent_query_id=None)
            queries.append(q);write(folder/'raw_access_queries.json',queries)
            try:actual=model.access(facility,q['window'])
            except Exception as exc:
                q.update(query_status='FAILED',error=repr(exc));write(folder/'raw_access_queries.json',queries);raise
            q.update(actual,query_status='COMPLETE',raw_event_count=actual['event_count'],unclipped_intervals=actual['intervals'])
            clipped=[]
            for e in actual['intervals']:
                a=max(window[0],e['start_epsec']);b=min(window[1],e['stop_epsec'])
                if a<b:clipped.append(dict(start_epsec=a,stop_epsec=b,duration_s=b-a,
                    unclipped_start_epsec=e['start_epsec'],unclipped_stop_epsec=e['stop_epsec'],
                    clipped_start=a!=e['start_epsec'],clipped_stop=b!=e['stop_epsec'],
                    native_start_at_query_boundary=e['start_epsec']==q['window'][0],native_stop_at_query_boundary=e['stop_epsec']==q['window'][1]))
            q.update(intervals=clipped,event_count=len(clipped),result_window=list(window))
            c.update(query_status='COMPLETE',event_count=len(clipped));source.update(access_events=len(clipped),refinement_window=q['window'])
            o['queries'].append(qid);o['event_count']+=len(clipped)
            for e in clipped:
                r=dict(identity,**e,record_id=f'{prefix}_R{len(raw):05d}',sat_id=model.sat_id,point_id=o['point_id'],
                    query_id=qid,candidate_id=c['candidate_id'],tier_id=prefix,window=list(window),local_refinement_window=q['window'],event_status='REAL_SENSOR_ACCESS')
                raw.append(r)
                g=audit_geometry(model,facility,target,r);g['point_set_sha256']=point_hash
                g['medium_access'].update(query_type='MEDIUM_CONVERGENCE',parent_query_id=qid,record_id=r['record_id'],query_status='COMPLETE')
                geometry.append(g)
            for filename,value in [('raw_access_queries.json',queries),('raw_intervals.json',raw),('geometry_audit.json',geometry),('candidate_ledger.json',candidates)]:write(folder/filename,value)
        o['outcome_code']='REAL_IMAGING_EVENT' if o['event_count'] else 'CANDIDATES_REFINED_NO_EVENT' if o['queries'] else 'NO_REFINEMENT_CANDIDATE'
    for filename,value in [('opportunities.json',opportunities),('raw_access_queries.json',queries),('raw_intervals.json',raw),('geometry_audit.json',geometry),('candidate_ledger.json',candidates)]:write(folder/filename,value)
    write(folder/'entry_evidence.json',dict(entry=ENTRY,kind=kind,run_id=ctx['run_id'],code_sha256=ctx['code_sha256'],
        evaluation_window=window,screening_window=screening,step_s=step,phase_s=phase,padding_s=pad,
        candidates=len(candidates),queries=len(queries),events=len(raw),history_seeds_accepted=False,
        deduplication_rule='Same pair successive exact roots < 0.01 s; all coarse source indices retained; no event deletion',
        retries='None',native_query_clipping='Local query inside propagated scenario; preserve native intervals then intersect evaluation window'))
    return dict(opportunities=opportunities,candidates=candidates,queries=queries,raw=raw,geometry=geometry,
        screening_s=screening_s,access_and_geometry_s=time.perf_counter()-fine_start)

"""One round7 candidate-to-native-Access chain for every current workload."""
from pathlib import Path
import time
import numpy as np
from src.round2.evidence import KEYS,write,digest
from src.round3.experiments import Model as BaseModel
from src.round4.experiments import audit_geometry
from .candidates import cell_windows,normalize_sources

ENTRY='src.round7.production.search_batch'


class Model(BaseModel):
    def __init__(self,*args,**kwargs):
        self.trajectory_sequence=0
        self.profile=dict(provider_requests=0,provider_states=0,transform_requests=0,
            exact_state_requests=0,exact_cache_hits=0,exact_state_s=0.,bulk_provider_s=0.,basis_s=0.,trajectory_write_s=0.,
            propagation_commands_s=0.,other_setup_commands_s=0.)
        super().__init__(*args,**kwargs)

    def cmd(self,command):
        start=time.perf_counter();r=super().cmd(command)
        self.profile['propagation_commands_s' if command.startswith('SetState ') else 'other_setup_commands_s']+=time.perf_counter()-start
        return r

    def vector(self,*args):
        self.profile['provider_requests']+=1;self.profile['provider_states']+=1
        return super().vector(*args)

    def matrix(self,*args):
        self.profile['transform_requests']+=1
        return super().matrix(*args)

    def state(self,t):
        self.profile['exact_state_requests']+=1
        self.profile['exact_cache_hits']+=int(float(t) in self.cache)
        started=time.perf_counter();r=super().state(t)
        self.profile['exact_state_s']+=time.perf_counter()-started
        return r

    def coarse_states(self,lo,hi,step,phase):
        key=(lo,hi,step,phase)
        if key in self.coarse_cache:return self.coarse_cache[key]
        interior=np.arange(lo+phase,hi,step,dtype=float)
        requested=np.concatenate(([lo],interior[(interior>lo+1e-8)&(interior<hi-1e-8)],[hi]))
        started=time.perf_counter();blocks=[]
        for provider in ['Cartesian Position','Cartesian Velocity']:
            data=self.sat.DataProviders.Item(provider).Group.Item('Fixed').ExecSingleElementsArray(requested.tolist(),['Time','x','y','z'])
            self.calls+=1;self.profile['provider_requests']+=1;self.profile['provider_states']+=len(requested)
            if not data.Valid or data.Count!=4:raise ValueError('Invalid explicit bulk provider')
            blocks.append({k:np.asarray(data.GetArray(k),dtype=float) for k in ['Time','x','y','z']})
        self.profile['bulk_provider_s']+=time.perf_counter()-started
        times=blocks[0]['Time']
        if any(b['Time'].shape!=requested.shape or not np.allclose(b['Time'],requested,rtol=0,atol=1e-8) for b in blocks):raise ValueError('Actual explicit trajectory grid mismatch')
        if not np.array_equal(times,blocks[1]['Time']) or not np.all(np.diff(times)>0):raise ValueError('Trajectory ordering mismatch')
        pos=np.column_stack([blocks[0][k] for k in 'xyz']);vel=np.column_stack([blocks[1][k] for k in 'xyz'])
        if pos.shape!=(len(times),3) or vel.shape!=pos.shape or not np.all(np.isfinite(pos)) or not np.all(np.isfinite(vel)):raise ValueError('Trajectory state dimensions/nonfinite')
        started=time.perf_counter();omega=np.array([0.,0.,self.cfg['round3']['search']['coarse_earth_rotation_rad_s']])
        z=-pos/np.linalg.norm(pos,axis=1)[:,None];v=vel+np.cross(omega,pos)
        x=v-np.einsum('ij,ij->i',v,z)[:,None]*z;x/=np.linalg.norm(x,axis=1)[:,None]
        y=np.cross(z,x);y/=np.linalg.norm(y,axis=1)[:,None];bases=np.stack([x,y,z],axis=2)
        self.profile['basis_s']+=time.perf_counter()-started
        calibration=[]
        for index in [0,len(times)-1]:
            ep,eb=self.state(times[index]);calibration.append(dict(time_epsec=float(times[index]),position_error_km=float(np.linalg.norm(pos[index]-ep)),axis_error=float(np.max(np.linalg.norm(bases[index]-eb,axis=0)))))
        if max(c['axis_error'] for c in calibration)>self.cfg['round3']['search']['coarse_axis_tolerance']:raise ValueError('Screening basis calibration failed')
        path=self.folder/'coarse_trajectory'/f'{self.trajectory_sequence:03d}.json';self.trajectory_sequence+=1;started=time.perf_counter()
        write(path,dict(**{k:self.ctx[k] for k in KEYS},sat_id=self.sat_id,window=[lo,hi],step_s=step,phase_s=phase,
            calibration=calibration,times_epsec=times.tolist(),requested_times_epsec=requested.tolist(),positions_fixed_km=pos.tolist(),
            bases_screening=bases.tolist(),method='Explicit STK Fixed r/v; vectorized frozen omega-cross-r basis; candidate screening only'))
        ref=dict(path=str(path),sha256=digest(path.read_bytes()),calibration=calibration)
        self.profile['trajectory_write_s']+=time.perf_counter()-started
        self.coarse_cache[key]=(times.tolist(),pos,bases,ref)
        return self.coarse_cache[key]


def screen(model,target,window,step,phase):
    start=time.perf_counter();times,pos,bases,ref=model.coarse_states(*window,step,phase)
    delta=target-np.asarray(pos);body=np.einsum('nji,nj->ni',bases,delta)
    ranges=np.linalg.norm(delta,axis=1)
    normal=np.array([target[0]/model.a**2,target[1]/model.a**2,target[2]/model.b**2]);normal/=np.linalg.norm(normal)
    elevation=np.degrees(np.arcsin(np.clip(-np.einsum('ij,j->i',delta,normal)/ranges,-1,1)))
    sources,counts=cell_windows(times,body,model.cam['along_half_deg'],2*model.cam['cross_half_deg'],model.cfg['round7']['candidate_guard_km'])
    # Preserve the old horizon guard as a candidate rejection, after enclosure.
    for s in sources:
        i=s['cell_index'];s['sample_max_elevation_deg']=float(max(elevation[i],elevation[i+1]))
        if s['possible_intervals'] and s['sample_max_elevation_deg'] < -model.cfg['round4']['horizon_guard_deg']:
            s['possible_intervals']=[];s['reason_code']='SAMPLED_BELOW_HORIZON_WITH_GUARD'
    return dict(candidates=sources,cell_dispositions=counts,trajectory=ref,coarse_samples=len(times),coarse_step_s=step,phase_s=phase,
        max_sampled_elevation_deg=float(max(elevation)),max_elevation_time_epsec=float(times[int(np.argmax(elevation))]),
        closest_range_km=float(min(ranges)),closest_range_time_epsec=float(times[int(np.argmin(ranges))]),visible_samples=int(sum(elevation>=0)),
        coarse_crossings=len(sources),exact_candidates=0,screen_elapsed_s=time.perf_counter()-start)


def search_batch(models,points,window,step,phase,folder,point_hash,prefix,kind):
    start=time.perf_counter();folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    ctx=models[0].ctx;cfg=models[0].cfg;settings=cfg['round3']['search'];pad=settings['padding_s']
    ident={**{k:ctx[k] for k in KEYS},'point_set_sha256':point_hash,'sensor_id':'Camera'}
    screening=[max(0.,window[0]-pad),min(cfg['duration_s'],window[1]+pad)]
    opportunities=[];candidates=[];queries=[];raw=[];geometry=[];write_s=0.
    def save(name,value):
        nonlocal write_s
        t=time.perf_counter();write(folder/name,value);write_s+=time.perf_counter()-t
    modelmap={m.sat_id:m for m in models};pointmap={p[0]:p[1:] for p in points};before=sum(m.access_calls for m in models)
    for model in models:
        for pi,(pid,fac,target) in enumerate(points):
            o=screen(model,target,screening,step,phase)
            o.update(ident,sat_id=model.sat_id,point_id=pid,window=list(window),evaluation_window=list(window),screening_window=screening,padding_s=pad,entry=ENTRY,queries=[],event_count=0)
            pair=f'{prefix}_{model.sat_id}_{pid}'
            for i,s in enumerate(o['candidates']):s['source_id']=f'{pair}_S{i:05d}';s['candidate_ids']=[]
            groups=normalize_sources(o['candidates'],settings['candidate_half_window_s'],cfg['duration_s'])
            for gi,g in enumerate(groups):
                cid=f'{pair}_C{gi:05d}';ss=[o['candidates'][i] for i in g['source_indices']]
                for s in ss:s['candidate_ids'].append(cid)
                candidates.append(dict(ident,candidate_id=cid,sat_id=model.sat_id,point_id=pid,
                    local_query_window=g['window'],evaluation_window=list(window),screening_window=screening,
                    source_ids=[s['source_id'] for s in ss],query_status='PENDING',candidate_root_epsec=None,
                    normalization='Union of overlapping/touching padded possible intervals; no proximity root deletion'))
            o['refinement_candidates']=len(groups);opportunities.append(o)
            print('SCREEN',prefix,model.sat_id,pi+1,len(points),len(groups),flush=True)
        save('opportunities_screened.json',dict(run_id=ctx['run_id'],complete=len(opportunities)==len(models)*len(points),pairs=opportunities))
        # Every point for this satellite is already screened and references a
        # persisted trajectory. Fine Access never uses these coarse arrays.
        # Monotonic trajectory_sequence prevents later diagnostic overwrites.
        if hasattr(model,'coarse_cache'):model.coarse_cache.clear()
    screening_s=time.perf_counter()-start
    save('screen_complete_before_access.json',dict(run_id=ctx['run_id'],pairs=len(opportunities),access_calls=sum(m.access_calls for m in models)-before,elapsed_s=screening_s,entry=ENTRY))
    save('candidate_ledger.json',candidates);fine=time.perf_counter()
    for o in opportunities:
        model=modelmap[o['sat_id']];fac,target=pointmap[o['point_id']]
        for c in [x for x in candidates if x['sat_id']==o['sat_id'] and x['point_id']==o['point_id']]:
            qid=f'{prefix}_Q{len(queries):05d}';c['query_id']=qid
            q=dict(ident,query_id=qid,candidate_id=c['candidate_id'],sat_id=model.sat_id,point_id=o['point_id'],evaluation_window=list(window),
                window=c['local_query_window'],query_type=kind,query_status='STARTED',candidate_root_epsec=None,parent_query_id=None)
            queries.append(q);save('raw_access_queries.json',queries)
            try:a=model.access(fac,q['window'])
            except Exception as exc:
                q.update(query_status='FAILED',error=repr(exc));save('raw_access_queries.json',queries);raise
            q.update(a,query_status='COMPLETE',raw_event_count=a['event_count'],unclipped_intervals=a['intervals'])
            clipped=[]
            for e in a['intervals']:
                lo=max(window[0],e['start_epsec']);hi=min(window[1],e['stop_epsec'])
                if lo<hi:clipped.append(dict(start_epsec=lo,stop_epsec=hi,duration_s=hi-lo,unclipped_start_epsec=e['start_epsec'],unclipped_stop_epsec=e['stop_epsec'],
                    clipped_start=lo!=e['start_epsec'],clipped_stop=hi!=e['stop_epsec']))
            q.update(intervals=clipped,event_count=len(clipped),result_window=list(window));c.update(query_status='COMPLETE',event_count=len(clipped))
            o['queries'].append(qid);o['event_count']+=len(clipped)
            for e in clipped:
                r=dict(ident,**e,record_id=f'{prefix}_R{len(raw):05d}',sat_id=model.sat_id,point_id=o['point_id'],query_id=qid,
                    candidate_id=c['candidate_id'],tier_id=prefix,window=list(window),local_refinement_window=q['window'],event_status='REAL_SENSOR_ACCESS')
                raw.append(r);g=audit_geometry(model,fac,target,r);g['point_set_sha256']=point_hash
                g['medium_access'].update(query_type='MEDIUM_CONVERGENCE',parent_query_id=qid,record_id=r['record_id'],query_status='COMPLETE');geometry.append(g)
            for name,value in [('raw_access_queries.json',queries),('raw_intervals.json',raw),('geometry_audit.json',geometry),('candidate_ledger.json',candidates)]:save(name,value)
        o['outcome_code']='REAL_IMAGING_EVENT' if o['event_count'] else 'CANDIDATES_REFINED_NO_EVENT' if o['queries'] else 'NO_REFINEMENT_CANDIDATE'
    for name,value in [('opportunities.json',opportunities),('raw_access_queries.json',queries),('raw_intervals.json',raw),('geometry_audit.json',geometry),('candidate_ledger.json',candidates)]:save(name,value)
    profile=dict(screening_s=screening_s,access_and_geometry_s=time.perf_counter()-fine,evidence_write_s=write_s,
        models={m.sat_id:m.profile for m in models},entry=ENTRY,kind=kind,history_seeds_accepted=False,
        scope='Cubic enclosure plus 0.05 km guard is validated on declared finite fixtures; not a seven-day zero-miss proof')
    save('entry_evidence.json',profile)
    return dict(opportunities=opportunities,candidates=candidates,queries=queries,raw=raw,geometry=geometry,
        screening_s=screening_s,access_and_geometry_s=profile['access_and_geometry_s'])

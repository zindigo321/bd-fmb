"""Offline timing/candidate retention; interpolated states are not event truth."""
from pathlib import Path
import time
import numpy as np
from src.round2.evidence import read,write,digest
from src.round2.geometry import geodetic_to_ecef
from src.round4.experiments import screen as old_screen
from .production import screen as new_screen
from .candidates import normalize_sources


def replay(cfg,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);prior=Path(cfg['round7']['source_round6'])
    opportunities=read(prior/'G/T6/opportunities.json');points=read(prior/'grid/frozen.json')['points'];events=read(prior/'G/T6/raw_intervals.json');rows=[];sources=[]
    for sat in cfg['round7']['original_seven']:
        ref=next(o['trajectory'] for o in opportunities if o['sat_id']==sat);p=Path(ref['path']);blob=p.read_bytes()
        if digest(blob)!=ref['sha256']:raise ValueError('Frozen trajectory source modified')
        data=read(p);times=np.array(data['times_epsec']);pos=np.array(data['positions_fixed_km']);bases=np.array(data['bases_screening'])
        if len(times)!=120961 or not np.array_equal(times,np.arange(0,604801,5)):raise ValueError('Offline source grid mismatch')
        sources.append(dict(path=str(p),sha256=ref['sha256'],sat_id=sat,count=len(times)))
        class Playback:
            def __init__(self):self.cfg=cfg;self.cam=cfg['camera'];self.a=cfg['earth']['ellipsoid_a_km'];self.b=cfg['earth']['ellipsoid_b_km'];self.cache={};self.calls=0
            def coarse_states(self,*args):return times.tolist(),pos,bases,ref
            def state(self,t):
                self.calls+=1
                if t not in self.cache:
                    i=min(max(int(np.searchsorted(times,t))-1,0),len(times)-2);u=(t-times[i])/(times[i+1]-times[i])
                    self.cache[t]=(pos[i]*(1-u)+pos[i+1]*u,bases[i]*(1-u)+bases[i+1]*u)
                return self.cache[t]
        for pt in points:
            target=geodetic_to_ecef(pt['latitude_deg'],pt['longitude_deg'],0,cfg['earth']['ellipsoid_a_km'],cfg['earth']['ellipsoid_b_km'])
            oldmodel=Playback();t=time.perf_counter();old=old_screen(oldmodel,target,[0.,604800.],0.,5.,True);old_s=time.perf_counter()-t
            model=Playback();t=time.perf_counter();new=new_screen(model,target,[0.,604800.],5.,0.);groups=normalize_sources(new['candidates'],.05,604800);new_s=time.perf_counter()-t
            historical=[e for e in events if (e['sat_id'],e['point_id'])==(sat,pt['point_id'])]
            retain=[dict(record_id=e['record_id'],retained=any(w['window'][0]<=e['start_epsec'] and w['window'][1]>=e['stop_epsec'] for w in groups)) for e in historical]
            oldroots=[c['exact_root_epsec'] for c in old['candidates'] if c['reason_code']=='REFINE_SENSOR_ACCESS']
            oldretained=[any(w['window'][0]<=r<=w['window'][1] for w in groups) for r in oldroots]
            rows.append(dict(sat_id=sat,point_id=pt['point_id'],old_candidate_count=len(oldroots),new_candidate_count=len(groups),old_roots_inside_new_windows=oldretained,
                new_windows=[g['window'] for g in groups],known_event_retention=retain,old_s=old_s,new_s=new_s,old_interpolated_state_requests=oldmodel.calls,new_interpolated_state_requests=model.calls))
        write(folder/'progress.json',dict(sat_id=sat,pairs=len(rows)));print('REPLAY',sat,len(rows),flush=True)
    retention=[e for r in rows for e in r['known_event_retention']]
    result=dict(status='PASS' if len(rows)==112 and len(retention)==12 and all(e['retained'] for e in retention) else 'FAIL',sources=sources,pairs=rows,
        old_total_s=sum(r['old_s'] for r in rows),new_total_s=sum(r['new_s'] for r in rows),events_recomputed=False,
        scope='Saved5s states, linear interpolation for old exact-root API; candidate retention/timing only. Neither millisecond truth nor end-to-end STK speedup.',
        candidate_difference_policy='All known12 native events must lie in new windows; old candidates are not truth. Extra windows are permitted and require native Access in current runs.')
    write(folder/'result.json',result);return result

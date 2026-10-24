"""Actual STK experiments. Reference construction is separate from search()."""
import csv
import math
from pathlib import Path
import re
import time
import numpy as np

from src.constellation import generate
from src.round2.evidence import canonical, digest, read, write
from src.round2.geometry import (angles, body_basis, quaternion_matrix, ray_ellipsoid,
    ecef_to_geodetic, geodetic_to_ecef, visible, bisect_crossing)
from .statistics import aggregate
from .refine import root as refine_root


def csv_out(path, rows, fields):
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


class Model:
    def __init__(self, root, ctx, cfg, folder, load=None, row=None, existing=False):
        self.root=root;self.ctx=ctx;self.cfg=cfg;self.folder=Path(folder)
        self.commands=[];self.cache={};self.coarse_cache={};self.calls=0;self.access_calls=0
        self.a=cfg['earth']['ellipsoid_a_km'];self.b=cfg['earth']['ellipsoid_b_km']
        self.cam=cfg['camera']
        if load and not existing:
            root.LoadScenario(str(load))
        elif not existing:
            root.NewScenario('R3_'+ctx['run_id'])
        root.UnitPreferences.SetCurrentUnit('DateFormat','UTCG')
        root.UnitPreferences.SetCurrentUnit('DistanceUnit','km')
        root.UnitPreferences.SetCurrentUnit('AngleUnit','deg')
        if not load and not existing:
            root.CurrentScenario.Epoch=cfg['epoch_utcg']
        root.UnitPreferences.SetCurrentUnit('DateFormat','EpSec')
        self.cmd('Units_Set * Connect Distance km Date GregorianUTC Time sec')
        row=row or generate(**cfg['constellation'],constants=cfg['earth'])[0][0]
        self.sat_id=row['sat_id']
        if not load and not existing:
            root.CurrentScenario.SetTimePeriod(0,cfg['duration_s'])
        if not load:
            start=cfg['epoch_utcg'];stop=root.ConversionUtility.ConvertDate('EpSec','UTCG',cfg['duration_s'])
            self.cmd('New / */Satellite '+self.sat_id)
            self.cmd(f'SetState */Satellite/{self.sat_id} Classical J2Perturbation "{start}" "{stop}" {cfg["propagation_step_s"]} J2000 "{start}" {cfg["earth"]["mean_radius_km"]+row["h_km"]:.14f} 0 {row["i_deg"]:.14f} 0 {row["raan_deg"]:.14f} {row["u_deg"]:.14f}')
            self.cmd(f'SetAttitude */Satellite/{self.sat_id} Profile NadirECIVel Offset 0')
            self.cmd(f'New / */Satellite/{self.sat_id}/Sensor Camera')
            self.cmd(f'Define */Satellite/{self.sat_id}/Sensor/Camera Rectangular {self.cam["cross_half_deg"]} {self.cam["along_half_deg"]}')
            self.cmd(f'Point */Satellite/{self.sat_id}/Sensor/Camera Fixed Quaternion 0 0 0 1')
        self.sat=root.GetObjectFromPath('Satellite/'+self.sat_id)
        self.sensor=root.GetObjectFromPath('Satellite/'+self.sat_id+'/Sensor/Camera')
        earth=root.CentralBodies.Item('Earth')
        self.fixed=earth.Vgt.Axes.Item('Fixed');self.j2000=earth.Vgt.Axes.Item('J2000')
        pj=self.vector('Cartesian Position','J2000',600)
        pf=self.vector('Cartesian Position','Fixed',600)
        matrix=self.matrix(self.j2000,self.fixed,600)
        self.transpose=np.linalg.norm(matrix.T@pj-pf)<np.linalg.norm(matrix@pj-pf)
        self.transform_error=float(min(np.linalg.norm(matrix@pj-pf),np.linalg.norm(matrix.T@pj-pf)))
        if self.transform_error>cfg['tolerances']['position_km']:
            raise ValueError('Coordinate transform calibration failed')

    def cmd(self, command):
        self.commands.append(command)
        write(self.folder/'connect_commands.json',dict(run_id=self.ctx['run_id'],commands=self.commands))
        result=self.root.ExecuteCommand(command)
        return [result.Item(i) for i in range(result.Count)]

    def vector(self, provider, frame, t):
        self.calls+=1
        data=self.sat.DataProviders.Item(provider).Group.Item(frame).ExecSingleElements(float(t),['x','y','z'])
        return np.array([float(data.DataSets.GetDataSetByName(k).GetValues()[0]) for k in 'xyz'])

    def matrix(self, axes, reference, t):
        self.calls+=1
        value=axes.FindInAxes(float(t),reference)
        if not value.IsValid:raise ValueError('Invalid axes')
        return quaternion_matrix(value.Orientation.QueryQuaternionArray())

    def state(self,t):
        t=float(t)
        if t not in self.cache:
            pj=self.vector('Cartesian Position','J2000',t)
            vj=self.vector('Cartesian Velocity','J2000',t)
            rotation=self.matrix(self.j2000,self.fixed,t)
            if self.transpose:rotation=rotation.T
            self.cache[t]=(rotation@pj,rotation@body_basis(pj,vj))
        return self.cache[t]

    def coarse_states(self,lo,hi,step,phase):
        """Bulk fixed-frame r/v, approximate inertial velocity for screening only.

        Every candidate is re-bracketed/refined using exact J2000+VGT state().
        The fixed-axis rotation approximation never supplies event boundaries.
        """
        key=(lo,hi,step,phase)
        if key in self.coarse_cache:return self.coarse_cache[key]
        start=lo+phase if phase else lo
        blocks=[]
        for provider in ['Cartesian Position','Cartesian Velocity']:
            data=self.sat.DataProviders.Item(provider).Group.Item('Fixed').ExecElements(
                float(start),float(hi),float(step),['Time','x','y','z'])
            self.calls+=1
            blocks.append({k:np.asarray(data.DataSets.GetDataSetByName(k).GetValues(),dtype=float) for k in ['Time','x','y','z']})
        if not np.array_equal(blocks[0]['Time'],blocks[1]['Time']):raise ValueError('Bulk trajectory time mismatch')
        times=blocks[0]['Time'].tolist()
        positions=np.column_stack([blocks[0][k] for k in 'xyz'])
        velocities=np.column_stack([blocks[1][k] for k in 'xyz'])
        omega=np.array([0.,0.,self.cfg['round3']['search']['coarse_earth_rotation_rad_s']])
        bases=[body_basis(p,v+np.cross(omega,p)) for p,v in zip(positions,velocities)]
        calibration=[]
        for index in sorted(set([0,len(times)-1])):
            ep,eb=self.state(times[index])
            calibration.append(dict(time_epsec=times[index],position_error_km=float(np.linalg.norm(positions[index]-ep)),
                axis_error=float(np.max(np.linalg.norm(bases[index]-eb,axis=0)))))
        if max(x['axis_error'] for x in calibration)>self.cfg['round3']['search']['coarse_axis_tolerance']:
            raise ValueError('Coarse rotation approximation exceeds declared screening tolerance')
        if times[0]>lo:
            p,b=self.state(lo);times.insert(0,lo);positions=np.vstack([p,positions]);bases.insert(0,b)
        if times[-1]<hi:
            p,b=self.state(hi);times.append(hi);positions=np.vstack([positions,p]);bases.append(b)
        evidence=dict(window=[lo,hi],step_s=step,phase_s=phase,calibration=calibration,
            method='Bulk STK Fixed position/velocity; approximate inertial v = v_fixed + omega cross r; exact state rebracketing for candidates',
            times_epsec=times,positions_fixed_km=positions.tolist(),bases_screening=[b.tolist() for b in bases])
        path=self.folder/'coarse_trajectory'/f'{len(self.coarse_cache):03d}.json';write(path,evidence)
        self.coarse_cache[key]=(times,positions,bases,dict(path=str(path),calibration=calibration))
        return self.coarse_cache[key]

    def facility(self,name,lat,lon):
        self.cmd('New / */Facility '+name)
        facility=self.root.GetObjectFromPath('Facility/'+name)
        facility.UseTerrain=False
        facility.Position.AssignGeodetic(lat,lon,0)
        target=geodetic_to_ecef(lat,lon,0,self.a,self.b)
        readback=np.array(facility.Position.QueryCartesianArray(),dtype=float)
        data=dict(point_id=name,latitude_deg=lat,longitude_deg=lon,altitude_km=0,
                  use_terrain=bool(facility.UseTerrain),cartesian_km=readback.tolist(),
                  error_km=float(np.linalg.norm(target-readback)))
        if data['use_terrain'] or data['error_km']>self.cfg['tolerances']['position_km']:
            raise ValueError('Facility surface readback mismatch')
        return facility,target,data

    def diagnostic(self,name,t,cross=0,along=0):
        p,basis=self.state(t)
        ray=basis@np.array([math.tan(math.radians(along*self.cam['along_half_deg'])),
                           math.tan(math.radians(cross*self.cam['cross_half_deg'])),1])
        target=ray_ellipsoid(p,ray,self.a,self.b)
        lat,lon,h=ecef_to_geodetic(target,self.a,self.b)
        fac,target,info=self.facility(name,lat,lon)
        later,_=self.state(t+.1);earlier,_=self.state(t-.1)
        info.update(purpose='DIAGNOSTIC',reference_seed_epsec=t,cross_half_multiplier=cross,
                    along_half_multiplier=along,visible=visible(p,target,self.a,self.b),
                    direction='ASCENDING' if later[2]>earlier[2] else 'DESCENDING')
        return fac,target,info

    def access(self,facility,window,tier=None):
        settings=tier or self.cfg['round3']['search']
        access=self.sensor.GetAccessToObject(facility)
        access.RemoveAccess()
        # STK 11 cannot query the interval collection before any computation
        # (Invalid dimension name). Release and reacquire after explicit removal.
        access=None
        access=self.sensor.GetAccessToObject(facility)
        access.AccessTimePeriod=2
        access.SpecifyAccessTimePeriod(*map(float,window))
        advanced=access.Advanced
        advanced.UseFixedTimeStep=False;advanced.UsePreciseEventTimes=True
        advanced.MinTimeStep=settings['min_step_s'];advanced.MaxTimeStep=settings['max_step_s']
        advanced.TimeConvergence=settings['time_convergence_s'];advanced.EnableLightTimeDelay=False
        rb=dict(min_step_s=float(advanced.MinTimeStep),max_step_s=float(advanced.MaxTimeStep),
                time_convergence_s=float(advanced.TimeConvergence),precise_events=bool(advanced.UsePreciseEventTimes),
                fixed_step=bool(advanced.UseFixedTimeStep),light_time=bool(advanced.EnableLightTimeDelay))
        if any(abs(rb[k]-settings[k])>1e-14 for k in ['min_step_s','max_step_s','time_convergence_s']):
            raise ValueError('Access settings did not take effect')
        start=time.perf_counter();self.access_calls+=1;access.ComputeAccess();elapsed=time.perf_counter()-start
        count=int(access.ComputedAccessIntervalTimes.Count)
        intervals=[]
        if count:
            data=access.DataProviders.Item('Access Data').Exec(*map(float,window))
            starts=data.DataSets.GetDataSetByName('Start Time').GetValues()
            stops=data.DataSets.GetDataSetByName('Stop Time').GetValues()
            intervals=[dict(start_epsec=float(a),stop_epsec=float(b),duration_s=float(b)-float(a)) for a,b in zip(starts,stops)]
        if len(intervals)!=count or any(not window[0]-1e-8<=x['start_epsec']<x['stop_epsec']<=window[1]+1e-8 for x in intervals):
            raise ValueError('Invalid Access result')
        access.RemoveAccess()
        return dict(window=list(window),cache_invalidation='RemoveAccess succeeded; released and reacquired; ComputeAccess called',event_count=count,intervals=intervals,
                    settings_readback=rb,elapsed_s=elapsed)

    def reference(self,target,seed):
        """Local angular reference, independently brackets both active edges.

        This intentionally knows the construction seed; search() never does.
        The seed is a known interior point; bisection, not coarse sampling,
        identifies its two boundaries. Negative seeds have local angular probes.
        """
        def margin(t):
            p,basis=self.state(t);x,y,z=angles(p,basis,target)
            return min(self.cam['along_half_deg']-abs(x),self.cam['cross_half_deg']-abs(y)) if z>0 else -1
        lo=seed-.05;hi=seed+.05
        center=margin(seed)
        if center<=0:
            samples=[margin(t) for t in np.linspace(lo,hi,11)]
            if max(samples)>0:raise ValueError('Reference negative seed has unexpected adjacent event')
            return dict(intervals=[],seed_margin_deg=center,local_window=[lo,hi],method='angular margin; negative local verification')
        if margin(lo)>=0 or margin(hi)>=0:raise ValueError('Reference not bracketed')
        start=refine_root(margin,lo,seed,tolerance=1e-9)
        stop=refine_root(margin,seed,hi,tolerance=1e-9)
        probes=[dict(time_epsec=t,margin_deg=margin(t)) for t in [start-2e-4,start+min(2e-4,(stop-start)/4),seed,stop-min(2e-4,(stop-start)/4),stop+2e-4]]
        return dict(intervals=[dict(start_epsec=start,stop_epsec=stop,duration_s=stop-start)],
            seed_margin_deg=center,local_window=[lo,hi],probes=probes,method='independent angular boundary safeguarded secant with verified 1e-9s bracket, no Access calls')

    def readback(self,points):
        prop=self.sat.Propagator
        rep=prop.InitialState.Representation.ConvertTo(1)
        rep.SizeShapeType=4;rep.LocationType=5;rep.Orientation.AscNodeType=1
        orbit=dict(a_km=float(rep.SizeShape.SemimajorAxis),e=float(rep.SizeShape.Eccentricity),
            i_deg=float(rep.Orientation.Inclination),arg_perigee_deg=float(rep.Orientation.ArgOfPerigee),
            raan_deg=float(rep.Orientation.AscNode.Value),true_anomaly_deg=float(rep.Location.Value))
        p,basis=self.state(600)
        actual=self.matrix(self.sat.Vgt.Axes.Item('Body'),self.fixed,600)
        if self.transpose:actual=actual.T
        point_info=[]
        for name in points:
            fac=self.root.GetObjectFromPath('Facility/'+name)
            point_info.append(dict(point_id=name,use_terrain=bool(fac.UseTerrain),
                geodetic=list(map(float,fac.Position.QueryPlanetodeticArray())),
                cartesian_km=list(map(float,fac.Position.QueryCartesianArray()))))
        return dict(satellite_count=sum(self.root.CurrentScenario.Children.Item(i).ClassName=='Satellite' for i in range(self.root.CurrentScenario.Children.Count)),
            initial_orbit=orbit,epoch_utcg=self.root.ConversionUtility.ConvertDate('EpSec','UTCG',0),
            orbit_epoch_epsec=float(prop.InitialState.Representation.Epoch),
            period_epsec=[float(self.root.CurrentScenario.StartTime),float(self.root.CurrentScenario.StopTime)],
            propagation_period_epsec=[float(prop.StartTime),float(prop.StopTime)],
            propagation_endpoint_positions_j2000_km=[self.vector('Cartesian Position','J2000',t).tolist() for t in [0,self.cfg['duration_s']]],
            satellite_constraints=[self.sat.AccessConstraints.Item(i).ConstraintName for i in range(self.sat.AccessConstraints.Count)],
            sensor_constraints=[self.sensor.AccessConstraints.Item(i).ConstraintName for i in range(self.sensor.AccessConstraints.Count)],
            propagator_type=int(self.sat.PropagatorType),step_s=float(prop.Step),
            initial_cartesian_j2000_km=self.vector('Cartesian Position','J2000',0).tolist(),
            initial_velocity_j2000_km_s=self.vector('Cartesian Velocity','J2000',0).tolist(),
            attitude_body_to_fixed=actual.tolist(),attitude_axis_error=float(np.max(np.abs(actual-basis))),
            horizontal_half_deg=float(self.sensor.Pattern.HorizontalHalfAngle),
            vertical_half_deg=float(self.sensor.Pattern.VerticalHalfAngle),
            mount_xyzw=list(map(float,self.sensor.Pointing.Orientation.QueryQuaternionArray())),points=point_info)


def search(model, facility, target, window, step, phase=0.0):
    """Deployable geometry coarse-screen + REAL Sensor Access refinement.

    Inputs contain no seed, reference event, or precomputed bracket. Find all
    along-axis zero crossings with positive boresight distance. Candidate pads
    are fixed configuration values. Finite coarse sampling is not a global proof.
    """
    started=time.perf_counter();calls_before=model.calls
    cfg=model.cfg['round3']['search'];pad=cfg['padding_s']
    lo=max(0,window[0]-pad);hi=min(model.cfg['duration_s'],window[1]+pad)
    times,positions,bases,coarse_evidence=model.coarse_states(lo,hi,step,phase)
    def along(t):
        p,b=model.state(t);v=b.T@(target-p)
        return float(v[0]),float(v[2]),float(v[1])
    values=[]
    for position,basis in zip(positions,bases):
        v=basis.T@(target-position);values.append((float(v[0]),float(v[2]),float(v[1])))
    roots=[]
    for ta,tb,va,vb in zip(times,times[1:],values,values[1:]):
        if min(va[1],vb[1])<=0:continue
        if va[0]*vb[0]>0:continue
        # Broaden one coarse step on both sides so the approximate rotating
        # frame cannot shift an exact root just outside the coarse bracket.
        exact_left=max(lo,ta-step);exact_right=min(hi,tb+step)
        fa=along(exact_left)[0];fb=along(exact_right)[0]
        if fa==0:root=exact_left
        elif fb==0:root=exact_right
        elif fa*fb<0:root=refine_root(lambda t:along(t)[0],exact_left,exact_right,cfg['root_tolerance_s'])
        else:raise ValueError('Approximate candidate has no exact root bracket; not silently discarded')
        _,z,cross=along(root)
        # Loose 2x cross-angle gate only reduces calls, it is not the event test.
        if z>0 and abs(math.degrees(math.atan2(cross,z)))<2*model.cam['cross_half_deg']:
            if not roots or abs(root-roots[-1])>.01:roots.append(root)
    queries=[];intervals=[]
    for root in roots:
        left=max(window[0],root-cfg['candidate_half_window_s'])
        right=min(window[1],root+cfg['candidate_half_window_s'])
        if left>=right:continue
        q=model.access(facility,[left,right]);q['candidate_root_epsec']=root
        queries.append(q)
        intervals.extend(q['intervals'])
    intervals.sort(key=lambda x:x['start_epsec'])
    return dict(window=list(window),coarse_step_s=step,sample_phase_s=phase,
        coarse_samples=len(times),candidate_roots=roots,queries=queries,intervals=intervals,
        event_count=len(intervals),elapsed_s=time.perf_counter()-started,
        state_provider_calls=model.calls-calls_before,
        coarse_trajectory=coarse_evidence,
        limitation='Along-axis sign-change candidates; no global guarantee for tangencies or arbitrary attitude')


def compare(reference, actual, window, tolerance):
    clipped=[]
    for item in reference:
        a=max(window[0],item['start_epsec']);b=min(window[1],item['stop_epsec'])
        if a<b:clipped.append(dict(start_epsec=a,stop_epsec=b,clipped_start=a>item['start_epsec'],clipped_stop=b<item['stop_epsec']))
    available=set(range(len(actual)));matches=[];missed=[]
    for i,r in enumerate(clipped):
        candidates=[j for j in available if max(abs(actual[j]['start_epsec']-r['start_epsec']),abs(actual[j]['stop_epsec']-r['stop_epsec']))<=tolerance]
        if not candidates:missed.append(i);continue
        j=min(candidates,key=lambda j:abs(actual[j]['start_epsec']-r['start_epsec']))
        available.remove(j);a=actual[j]
        matches.append(dict(reference_index=i,actual_index=j,start_error_s=a['start_epsec']-r['start_epsec'],
            stop_error_s=a['stop_epsec']-r['stop_epsec'],duration_error_s=(a['stop_epsec']-a['start_epsec'])-(r['stop_epsec']-r['start_epsec']),
            clipped_start=r['clipped_start'],clipped_stop=r['clipped_stop']))
    return dict(reference_count=len(clipped),detected_count=len(actual),matches=matches,missed=missed,
        extra=sorted(available),status='PASS' if not missed and not available else 'FAIL')


def reopen(model, payload, task):
    folder=model.folder
    if task=='save':
        positives=model.diagnostic('R2_center',600)
        negatives=model.diagnostic('R2_cross_out',600,1.5)
        write(folder/'diagnostic_points.json',[positives[2],negatives[2]])
    before=model.readback(['R2_center','R2_cross_out'])
    queries={}
    for name in ['R2_center','R2_cross_out']:
        queries[name]=model.access(model.root.GetObjectFromPath('Facility/'+name),[599.95,600.05])
    regression=[]
    for tier in model.cfg['access_tiers']:
        q=model.access(model.root.GetObjectFromPath('Facility/R2_center'),[599.95,600.05],tier)
        q['tier']=tier['name'];regression.append(q)
    write(folder/'round2_convergence_regression.json',regression)
    if any(q['event_count']!=1 for q in regression):
        raise ValueError('Round2 regression lost positive event')
    medium=regression[-2]['intervals'][0];fine=regression[-1]['intervals'][0]
    if max(abs(medium[k]-fine[k]) for k in ['start_epsec','stop_epsec','duration_s'])>model.cfg['tolerances']['convergence_s']:
        raise ValueError('Round2 medium/fine convergence tolerance failed')
    if queries['R2_center']['event_count']!=1 or queries['R2_cross_out']['event_count']!=0:
        raise ValueError('Reopen positive/negative expectation failed')
    project=folder/'project';project.mkdir()
    model.root.SaveScenarioAs(str(project/'Round3.sc'))
    saved=(project/'Sat001.sa').read_text(errors='replace')
    before['saved_j2']='BEGIN J2Perturbation' in saved
    before['saved_frame']=re.search(r'DisplayCoordSys\s+(\w+)',saved).group(1)
    before['saved_attitude_eci_vvlh']='BEGIN ECIVVLH' in saved
    write(folder/'parameters.json',before);write(folder/'access.json',queries)
    if before['step_s']!=model.cfg['propagation_step_s'] or not before['saved_j2'] or before['saved_frame']!='J2000':
        raise ValueError('Saved propagation model mismatch')
    return dict(parameters='parameters.json',access='access.json',project=str(project/'Round3.sc'))


def cases(model, pilot=False):
    specs=[('r2_center',600,0,None),('low_ascending',120.137,0,None),('mid_ascending',900.271,0,None),
           ('high_edge',1400.413,.8,None),('high_descending',1800.617,-.8,None),('low_descending',2600.731,0,None),
           ('south_descending',3100.193,0,None),('south_edge',3900.347,.98,None),('south_ascending',4700.529,0,None),
           ('late_phase',5400.863,-.98,None),('negative_north',900.337,1.5,None),('negative_descending',2600.419,-1.5,None),
           ('short_north',1800.351,.999999,None),('short_south',3900.717,-.999999,None),
           ('start_clipped',600.183,0,'start'),('end_clipped',2600.257,0,'end')]
    if pilot:specs=specs[:1]
    out=[];references=[];results=[]
    for index,(name,t,cross,clip) in enumerate(specs):
        case_started=time.perf_counter()
        fac,target,info=model.diagnostic('D_'+name,t,cross)
        ref=model.reference(target,t)
        if len(ref['intervals']) != (0 if abs(cross)>1 else 1):
            raise ValueError('Reference does not match declared construction expectation')
        info.update(case_id=name,expected='NEGATIVE' if abs(cross)>1 else 'POSITIVE',clip=clip)
        out.append(info);references.append(dict(case_id=name,**ref))
        # Windows are declared case fixtures; search receives only window and target.
        base=math.floor(t/60)*60-10
        variants=[(base,base+120,5,0),(base-3.713,base+133.431,2,.371),(base+1.219,base+124.537,1,.619)]
        if clip=='start':variants=[(t+.001,t+60+s,st,ph) for s,st,ph in [(0,5,0),(13.431,2,.371),(4.537,1,.619)]]
        if clip=='end':variants=[(t-60-s,t-.001,st,ph) for s,st,ph in [(0,5,0),(13.431,2,.371),(4.537,1,.619)]]
        for vi,(left,right,step,phase) in enumerate(variants):
            actual=search(model,fac,target,[left,right],step,phase)
            match=compare(ref['intervals'],actual['intervals'],[left,right],model.cfg['round3']['match_tolerance_s'])
            results.append(dict(case_id=name,variant=vi,**actual,comparison=match))
            write(model.folder/'search_results.json',results)
        same=results[-3:]
        if same[-1]['event_count']==same[-2]['event_count']==1:
            m=same[-2]['intervals'][0];f=same[-1]['intervals'][0]
            errors={k:abs(m[k]-f[k]) for k in ['start_epsec','stop_epsec','duration_s']}
            same[-1]['alignment_convergence']=dict(errors_s=errors,tolerance_s=model.cfg['tolerances']['convergence_s'],
                status='PASS' if max(errors.values())<=model.cfg['tolerances']['convergence_s'] else 'FAIL')
            if same[-1]['alignment_convergence']['status']!='PASS':
                same[-1]['comparison']['status']='FAIL'
        write(model.folder/'search_results.json',results)
        write(model.folder/'cases.json',out);write(model.folder/'references.json',references)
        print('CASE',index+1,len(specs),name,[r['comparison']['status'] for r in results[-3:]],flush=True)
        if index==0:
            cost=time.perf_counter()-case_started
            projected=cost*16+120
            write(model.folder/'cost_measurement.json',dict(first_case_elapsed_s=cost,projected_cases_plus_startup_budget_s=projected,
                stage_budget_s=model.cfg['round3']['stage_budget_s']['B'],method='First complete 3-variant case cost times 16 plus 120 seconds startup/cleanup allowance'))
            if projected>model.cfg['round3']['stage_budget_s']['B']:
                raise RuntimeError('Measured first-case cost exceeds declared batch budget')
    rows=[]
    for r in results:
        matches=r['comparison']['matches']
        rows.append(dict(case_id=r['case_id'],variant=r['variant'],reference_count=r['comparison']['reference_count'],
            detected_count=r['event_count'],missed=len(r['comparison']['missed']),extra=len(r['comparison']['extra']),
            max_start_error_s=max([abs(x['start_error_s']) for x in matches],default=0),
            max_stop_error_s=max([abs(x['stop_error_s']) for x in matches],default=0),
            max_duration_error_s=max([abs(x['duration_error_s']) for x in matches],default=0),
            clipped=any(x['clipped_start'] or x['clipped_stop'] for x in matches),elapsed_s=r['elapsed_s'],status=r['comparison']['status']))
    csv_out(model.folder/'matching.csv',rows,list(rows[0]))
    return dict(status='PASS' if all(r['status']=='PASS' for r in rows) else 'FAIL',cases=len(out),searches=len(results),
        shared_basis='Reference and search share STK trajectory and calibrated transform; independent reference attitude/angles versus Sensor Access',
        scope='Declared diagnostic set and tested windows/phases only; no global completeness proof')


def grid_access(model,payload):
    frozen=read(payload['grid'])
    if frozen['run_id']!=model.ctx['run_id'] or digest(canonical(frozen['points']))!=frozen['point_set_sha256']:
        raise ValueError('Frozen point ownership/hash mismatch')
    raw=[];queries=[];readbacks=[];duration=payload['duration_s'];start=time.perf_counter()
    for index,p in enumerate(frozen['points']):
        fac,target,rb=model.facility(p['point_id'],p['latitude_deg'],p['longitude_deg'])
        q=search(model,fac,target,[0,duration],5)
        # Also execute a full-window native Sensor Access for every fixed point,
        # including points whose geometry screen found no candidate. This coarse
        # query is evidence of real access calls, NOT the completeness reference.
        native=model.access(fac,[0,duration],dict(min_step_s=1.0,max_step_s=5.0,time_convergence_s=1e-6))
        q['native_full_window_access']=native
        for interval in native['intervals']:
            if not any(max(abs(interval['start_epsec']-x['start_epsec']),abs(interval['stop_epsec']-x['stop_epsec']))<=model.cfg['round3']['match_tolerance_s'] for x in q['intervals']):
                raise ValueError('Native full-window query found event absent from geometry-screen search')
        q.update(point_id=p['point_id'],sat_id='Sat001',sensor_id='Camera',run_id=model.ctx['run_id'])
        queries.append(q);readbacks.append(rb)
        for interval in q['intervals']:
            raw.append(dict(record_id=f'{model.ctx["run_id"]}_{len(raw):05d}',run_id=model.ctx['run_id'],
                point_id=p['point_id'],sat_id='Sat001',sensor_id='Camera',**interval,
                window_start_epsec=0,window_stop_epsec=duration,event_status='CLIPPED' if interval['start_epsec']==0 or interval['stop_epsec']==duration else 'COMPLETE',
                config_sha256=model.ctx['config_sha256'],point_set_sha256=frozen['point_set_sha256']))
        write(model.folder/'queries.json',queries);write(model.folder/'raw_intervals.json',raw)
        print('GRID',index+1,len(frozen['points']),p['point_id'],q['event_count'],round(time.perf_counter()-start,2),flush=True)
    stats=aggregate([p['point_id'] for p in frozen['points']],raw,[0,duration],model.cfg['round3']['grid']['merge_tolerance_s'])
    write(model.folder/'surface_readbacks.json',readbacks);write(model.folder/'statistics.json',stats)
    write(model.folder/'merged_intervals.json',[dict(point_id=p['point_id'],intervals=p['merged_intervals']) for p in stats['points']])
    fields=['record_id','run_id','point_id','sat_id','sensor_id','start_epsec','stop_epsec','duration_s','window_start_epsec','window_stop_epsec','event_status','config_sha256','point_set_sha256']
    csv_out(model.folder/'raw_intervals.csv',raw,fields)
    model.root.SaveScenarioAs(str(model.folder/'Grid.sc'))
    return dict(status='PASS',event_count=len(raw),duration_s=duration,points=len(frozen['points']),
        real_positive_aggregation_supported=bool(raw),elapsed_s=time.perf_counter()-start,
        state_provider_calls=model.calls,access_calls=model.access_calls,
        limitation='Small fixed subset, not complete 0.1 degree regional grid')


def dispatch(root,ctx,cfg,task,payload):
    model=Model(root,ctx,cfg,payload['folder'],load=payload.get('load'))
    try:
        if task in ['save','reopen']:answer=reopen(model,payload,task)
        elif task in ['cases','casepilot']:answer=cases(model,pilot=task=='casepilot')
        elif task.startswith('grid'):answer=grid_access(model,payload)
        else:raise ValueError('Unknown experiment task '+task)
        write(model.folder/'experiment.json',answer)
        if answer.get('status','PASS')!='PASS':raise ValueError('Experiment checks failed; inspect complete evidence')
        return answer
    finally:
        model.cache.clear()
        model.coarse_cache.clear()

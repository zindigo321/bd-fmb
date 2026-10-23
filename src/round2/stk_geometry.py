"""Actual STK geometry experiment, isolated process, no official coverage claims."""
import csv
import functools
import math
import re
from pathlib import Path
import time
import numpy as np
from .evidence import read,write,digest
from .geometry import unit,body_basis,quaternion_matrix,ray_ellipsoid,ecef_to_geodetic,geodetic_to_ecef,angles,visible,bisect_crossing

def csv_write(path,rows,fields=None):
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)

def run(ctx,cfg,check):
    import win32com.client,win32process,win32api,win32event
    folder=Path(ctx['output'])/'stk';folder.mkdir(exist_ok=True)
    env=read(Path(ctx['output'])/'environment/environment.json');install=Path(env['stk']['installation'])
    sources=[]
    names=['STKObjects~Enumerations~AgEAccessTimeType_EN.html','STKObjects~IAgStkAccess~SpecifyAccessTimePeriod.html','AgSTKVgtLib~IAgCrdnAxes~FindInAxes.html','STKUtil~IAgOrientation~QueryQuaternionArray.html','STKUtil~IAgPosition~QueryCartesianArray.html','STKObjects~IAgStkCentralBodyEllipsoid.html','STKObjects~IAgSnPtFixed~Orientation.html','STKObjects~IAgSnRectangularPattern.html','STKObjects~IAgStkAccessAdvanced.html','STKObjects~IAgFacility~UseTerrain.html']
    for name in names:
        path=install/'Help/Programming/Content/DocX'/name
        sources.append(dict(path=str(path),sha256=digest(path.read_bytes())))
    for name in ['cmd_New.htm','cmd_SetStateClassical.htm','cmd_DefineSensor.htm','cmd_SetAttitudeProfile.htm','cmd_PointFixed.htm']:
        path=install/'Help/Programming/Subsystems/connectCmds/Content'/name
        sources.append(dict(path=str(path),sha256=digest(path.read_bytes())))
    write(folder/'official_interfaces.json',dict(run_id=ctx['run_id'],sources=sources,user_spec_access_time_enum=2))
    app=None;owned=False;handle=None;log=[];cleanup={}
    try:
        before=set(win32process.EnumProcesses());app=win32com.client.DispatchEx(cfg['stk']['progid']);pid=int(app.ProcessID)
        if pid in before:raise RuntimeError('Cannot safely isolate: returned preexisting PID')
        # Process identity comes from this new COM object and is retained for cleanup verification.
        handle=win32api.OpenProcess(0x00100000|0x1000|0x0001,False,pid)
        root=app.Personality2;root.Isolate()
        if root.CurrentScenario is not None:raise RuntimeError('New instance contains a scenario; not touched')
        owned=True;app.Visible=False;app.UserControl=False
        ownership=dict(run_id=ctx['run_id'],pid=pid,absent_from_before=True,empty_scenario=True,executable=env['stk']['executable'])
        write(folder/'ownership.json',ownership)
        def cmd(text):
            log.append(text);write(folder/'connect_commands.json',dict(run_id=ctx['run_id'],commands=log));print(text,flush=True)
            res=root.ExecuteCommand(text);return [res.Item(i) for i in range(res.Count)]
        version=cmd('GetSTKVersion / Details')
        root.NewScenario('R2_'+ctx['run_id'].replace('-','_'))
        root.UnitPreferences.SetCurrentUnit('DateFormat','UTCG');root.UnitPreferences.SetCurrentUnit('DistanceUnit','km');root.UnitPreferences.SetCurrentUnit('AngleUnit','deg')
        start=cfg['epoch_utcg'];root.CurrentScenario.Epoch=start
        root.UnitPreferences.SetCurrentUnit('DateFormat','EpSec')
        root.CurrentScenario.SetTimePeriod(0,cfg['duration_s'])
        stop=root.ConversionUtility.ConvertDate('EpSec','UTCG',cfg['duration_s'])
        cmd('Units_Set * Connect Distance km Date GregorianUTC Time sec')
        with (Path(ctx['output'])/'generate/orbits.csv').open(encoding='utf-8') as f:row=next(csv.DictReader(f))
        row={k:v if k=='sat_id' else float(v) for k,v in row.items()}
        satpath='*/Satellite/'+row['sat_id'];cmd('New / */Satellite '+row['sat_id'])
        sma=cfg['earth']['mean_radius_km']+row['h_km']
        cmd(f'SetState {satpath} Classical J2Perturbation "{start}" "{stop}" {cfg["propagation_step_s"]} {cfg["reference_frame"]} "{start}" {sma:.14f} 0 {row["i_deg"]:.14f} 0 {row["raan_deg"]:.14f} {row["u_deg"]:.14f}')
        cam=cfg['camera'];cmd(f'SetAttitude {satpath} Profile {cam["attitude_profile"]} Offset {cam["constraint_offset_deg"]}')
        cmd(f'New / {satpath}/Sensor Camera');cmd(f'Define {satpath}/Sensor/Camera Rectangular {cam["cross_half_deg"]} {cam["along_half_deg"]}')
        cmd(f'Point {satpath}/Sensor/Camera Fixed Quaternion '+' '.join(map(str,cam['mount_quaternion_xyzw'])))
        sat=root.GetObjectFromPath('Satellite/'+row['sat_id']);sensor=root.GetObjectFromPath('Satellite/'+row['sat_id']+'/Sensor/Camera')
        earth=root.CentralBodies.Item('Earth');fixed=earth.Vgt.Axes.Item('Fixed');j2000=earth.Vgt.Axes.Item('J2000')
        model=dict(run_id=ctx['run_id'],version=version,epoch_utcg=root.ConversionUtility.ConvertDate('EpSec','UTCG',0),period_epsec=[float(root.CurrentScenario.StartTime),float(root.CurrentScenario.StopTime)],mu_readback_km3_s2=earth.GravitationalParameter,ellipsoid_a_km=earth.Ellipsoid.A,ellipsoid_b_km=earth.Ellipsoid.C,mean_radius_readback_km=earth.Ellipsoid.MeanRadius,pattern=dict(horizontal_half_deg=sensor.Pattern.HorizontalHalfAngle,vertical_half_deg=sensor.Pattern.VerticalHalfAngle),mount_xyzw=list(sensor.Pointing.Orientation.QueryQuaternionArray()))
        models=[]
        for name in ['Earth.cb','EGM2008.grv']:
            p=install/'STKData/CentralBodies/Earth'/name
            models.append(dict(path=str(p),sha256=digest(p.read_bytes()),lines=[line for line in p.read_text(errors='replace').splitlines() if any(s in line for s in ['GravityModel','Gm ','RefDistance','2    0'])][:12]))
        model['installation_model_evidence']=models;model['generator_constants']=cfg['earth']
        model['difference_note']='Readback ellipsoid/GM and installation gravity model are recorded. Generator secular J2 constants are provisional; no system file changes, no claim that internal secular gravity variations were independently identified.'
        write(folder/'model_readback.json',model)
        a=cfg['earth']['ellipsoid_a_km'];b=cfg['earth']['ellipsoid_b_km'];tol=cfg['tolerances']
        check('configuration_readback',abs(model['ellipsoid_a_km']-a)<tol['position_km'] and abs(model['ellipsoid_b_km']-b)<tol['position_km'] and model['pattern']==dict(horizontal_half_deg=cam['along_half_deg'],vertical_half_deg=cam['cross_half_deg']) and np.linalg.norm(np.array(model['mount_xyzw'])-[0,0,0,1])<1e-12,model='model_readback.json')
        def vector(provider,frame,t):
            data=sat.DataProviders.Item(provider).Group.Item(frame).ExecSingleElements(float(t),['x','y','z'])
            return np.array([float(data.DataSets.GetDataSetByName(k).GetValues()[0]) for k in ['x','y','z']])
        def matrix(axes,reference,t):
            found=axes.FindInAxes(float(t),reference)
            if not found.IsValid:raise ValueError('Invalid VGT orientation')
            return quaternion_matrix(found.Orientation.QueryQuaternionArray())
        t0=cfg['diagnostic_time_s'];p0j=vector('Cartesian Position','J2000',t0);p0f=vector('Cartesian Position','Fixed',t0)
        raw=matrix(j2000,fixed,t0)
        direct_error=float(np.linalg.norm(raw@p0j-p0f));transpose_error=float(np.linalg.norm(raw.T@p0j-p0f))
        use_transpose=transpose_error<direct_error
        check('rotation_convention_calibration',min(direct_error,transpose_error)<tol['position_km'] and max(direct_error,transpose_error)>1,raw_error_km=direct_error,transpose_error_km=transpose_error,use_transpose=use_transpose)
        def orient(axes,ref,t):
            m=matrix(axes,ref,t);return m.T if use_transpose else m
        samples={}
        @functools.lru_cache(maxsize=1024)
        def state(t):
            pj=vector('Cartesian Position','J2000',t);vj=vector('Cartesian Velocity','J2000',t);pf=vector('Cartesian Position','Fixed',t)
            rotation=orient(j2000,fixed,t);expected=rotation@body_basis(pj,vj)
            transform_error=float(np.linalg.norm(rotation@pj-pf))
            actual_body=orient(sat.Vgt.Axes.Item('Body'),fixed,t)
            actual_sensor=orient(sensor.Vgt.Axes.Item('Body'),fixed,t)
            samples[t]=dict(time_epsec=t,j2000_position_km=pj.tolist(),j2000_velocity_km_s=vj.tolist(),fixed_position_km=pf.tolist(),j2000_to_fixed=rotation.tolist(),expected_body_to_fixed=expected.tolist(),stk_body_to_fixed=actual_body.tolist(),stk_sensor_to_fixed=actual_sensor.tolist(),transform_error_km=transform_error,body_axis_error=float(np.max(np.linalg.norm(expected-actual_body,axis=0))),sensor_axis_error=float(np.max(np.linalg.norm(expected-actual_sensor,axis=0))),orthogonality_error=float(np.max(np.abs(expected.T@expected-np.eye(3)))))
            return pf,expected,actual_sensor
        for t in np.arange(0,cfg['duration_s']+1e-9,cfg['report_sample_step_s']):state(float(t))
        p0,basis0,sensor0=state(t0)
        specs=[('center',0,0),('along_in',.5,0),('along_out',1.5,0),('along_negative',-.5,0),('cross_in',0,.5),('cross_out',0,1.5),('cross_negative',0,-.5)]
        points=[];targets={};facilities={}
        for name,along,cross in specs:
            ray=basis0@np.array([math.tan(math.radians(along*cam['along_half_deg'])),math.tan(math.radians(cross*cam['cross_half_deg'])),1.])
            target=ray_ellipsoid(p0,ray,a,b);lat,lon,h=ecef_to_geodetic(target,a,b)
            cmd('New / */Facility '+name);fac=root.GetObjectFromPath('Facility/'+name)
            terrain_before=bool(fac.UseTerrain)
            fac.UseTerrain=cfg['diagnostic_surface']['use_terrain']
            fac.Position.AssignGeodetic(lat,lon,cfg['diagnostic_surface']['altitude_km']);readback=np.array(fac.Position.QueryCartesianArray(),dtype=float)
            ax,ay,z=angles(p0,basis0,target);sx,sy,_=angles(p0,sensor0,readback)
            point=dict(name=name,purpose='DIAGNOSTIC_ONLY_NOT_REGION_GRID',selected_time_epsec=t0,along_half_multiplier=along,cross_half_multiplier=cross,latitude_deg=lat,longitude_deg=lon,altitude_km=0,x_fixed_km=float(target[0]),y_fixed_km=float(target[1]),z_fixed_km=float(target[2]),visible=visible(p0,target,a,b),along_angle_deg=ax,cross_angle_deg=ay,stk_sensor_along_deg=sx,stk_sensor_cross_deg=sy,point_error_km=float(np.linalg.norm(readback-target)),roundtrip_error_km=float(np.linalg.norm(geodetic_to_ecef(lat,lon,h,a,b)-target)),inside_at_representative_time=abs(along)<1 and abs(cross)<1)
            point['use_terrain_before']=terrain_before;point['use_terrain_readback']=bool(fac.UseTerrain)
            points.append(point);targets[name]=target;facilities[name]=fac
        csv_write(folder/'diagnostic_points.csv',points)
        check('points_visible_and_transform',all(p['visible'] and p['point_error_km']<=tol['position_km'] and p['roundtrip_error_km']<=tol['position_km'] for p in points))
        check('along_cross_mapping',all(abs(p['along_angle_deg']-p['stk_sensor_along_deg'])<tol['angle_deg'] and abs(p['cross_angle_deg']-p['stk_sensor_cross_deg'])<tol['angle_deg'] for p in points))
        left=t0-cfg['diagnostic_half_window_s'];right=t0+cfg['diagnostic_half_window_s']
        def margin(t,name='center'):
            p,basis,_=state(float(t));x,y,z=angles(p,basis,targets[name])
            return min(cam['along_half_deg']-abs(x),cam['cross_half_deg']-abs(y)) if z>0 else -1
        bracket=[margin(left),margin(t0),margin(right)]
        check('independent_bracket',bracket[0]<0 and bracket[1]>0 and bracket[2]<0,margin_deg=bracket)
        independent_start=bisect_crossing(margin,left,t0);independent_stop=bisect_crossing(margin,t0,right)
        # Probe event-adjacent samples, not only a second call to Access.
        probe_times=[independent_start-.0002,independent_start,independent_start+.0002,t0,independent_stop-.0002,independent_stop,independent_stop+.0002]
        probe=[dict(time_epsec=t,margin_deg=margin(t)) for t in probe_times]
        write(folder/'independent_events.json',dict(run_id=ctx['run_id'],method='ellipsoidal fixed target, real J2000 r/v, independent cross-product attitude, angular boundary bisection; no Access calls',start_epsec=independent_start,stop_epsec=independent_stop,duration_s=independent_stop-independent_start,bracket_epsec=[left,right],probes=probe,tolerance_s=tol['independent_event_s']))
        queries=[];interval_rows=[];convergence=[]
        for tier in cfg['access_tiers']:
            query_names=['center'] if tier['name']!='fine' else [p['name'] for p in points]
            for name in query_names:
                access=sensor.GetAccessToObject(facilities[name]);access.AccessTimePeriod=2 # eUserSpecAccessTime, verified STK11 enum document
                access.SpecifyAccessTimePeriod(left,right)
                advanced=access.Advanced;advanced.UseFixedTimeStep=False;advanced.UsePreciseEventTimes=True
                advanced.MaxTimeStep=tier['max_step_s'];advanced.MinTimeStep=tier['min_step_s'];advanced.TimeConvergence=tier['time_convergence_s'];advanced.EnableLightTimeDelay=False
                started=time.perf_counter();access.ComputeAccess();elapsed=time.perf_counter()-started
                count=access.ComputedAccessIntervalTimes.Count;intervals=[]
                if count:
                    data=access.DataProviders.Item('Access Data').Exec(left,right)
                    starts=data.DataSets.GetDataSetByName('Start Time').GetValues();stops=data.DataSets.GetDataSetByName('Stop Time').GetValues()
                    for st,sp in zip(starts,stops):
                        st=float(st);sp=float(sp)
                        if not math.isfinite(st+sp) or not left<=st<sp<=right:raise ValueError('Invalid positive interval')
                        intervals.append(dict(start_epsec=st,stop_epsec=sp,duration_s=sp-st))
                        interval_rows.append(dict(run_id=ctx['run_id'],tier=tier['name'],point=name,**intervals[-1]))
                if len(intervals)!=count:raise ValueError('Access intervals/provider count mismatch')
                q=dict(point=name,tier=tier['name'],event_count=count,intervals=intervals,elapsed_s=elapsed,readback=dict(max_step_s=advanced.MaxTimeStep,min_step_s=advanced.MinTimeStep,time_convergence_s=advanced.TimeConvergence,precise_events=advanced.UsePreciseEventTimes,light_time=advanced.EnableLightTimeDelay))
                queries.append(q);write(folder/'access_raw.json',dict(run_id=ctx['run_id'],window=[left,right],queries=queries))
                print('ACCESS',name,tier['name'],intervals,flush=True)
                if name=='center':convergence.append(dict(tier=tier['name'],**{k:v for k,v in tier.items() if k!='name'},count=count,elapsed_s=elapsed,start_epsec=intervals[0]['start_epsec'] if count==1 else None,stop_epsec=intervals[0]['stop_epsec'] if count==1 else None,duration_s=intervals[0]['duration_s'] if count==1 else None))
        fields=['run_id','tier','point','start_epsec','stop_epsec','duration_s'];csv_write(folder/'access_intervals.csv',interval_rows,fields)
        csv_write(folder/'negative_access_intervals.csv',[r for r in interval_rows if r['point']=='cross_out'],fields)
        csv_write(folder/'convergence.csv',convergence)
        with (folder/'access_intervals.csv').open() as f:exported=list(csv.DictReader(f))
        check('positive_export',len(exported)==len(interval_rows) and len(exported)>0 and all(float(a['start_epsec'])==b['start_epsec'] and float(a['stop_epsec'])==b['stop_epsec'] for a,b in zip(exported,interval_rows)))
        check('negative_visible_outside',next(q for q in queries if q['point']=='cross_out')['event_count']==0 and next(p for p in points if p['name']=='cross_out')['visible'])
        counts_ok=all(c['count']==1 for c in convergence)
        check('positive_all_tiers',counts_ok,counts=[c['count'] for c in convergence])
        if counts_ok:
            deltas={key:abs(convergence[-1][key]-convergence[-2][key]) for key in ['start_epsec','stop_epsec','duration_s']}
            check('event_convergence',max(deltas.values())<=tol['convergence_s'],deltas_s=deltas,tolerance_s=tol['convergence_s'])
            errors=[abs(convergence[-1]['start_epsec']-independent_start),abs(convergence[-1]['stop_epsec']-independent_stop)]
            check('independent_event_agreement',max(errors)<=tol['independent_event_s'],errors_s=errors,tolerance_s=tol['independent_event_s'])
        # Ground footprint at four half-angle edges, on the ellipsoid, not a rectangle replacement of any region.
        edges={}
        for name,x,y in [('along_plus',1,0),('along_minus',-1,0),('cross_plus',0,1),('cross_minus',0,-1)]:
            edges[name]=ray_ellipsoid(p0,basis0@np.array([math.tan(math.radians(x*cam['along_half_deg'])),math.tan(math.radians(y*cam['cross_half_deg'])),1]),a,b)
        center=targets['center'];normal=unit(center/np.array([a*a,a*a,b*b]));radial=unit(center)
        footprint=dict(run_id=ctx['run_id'],edges_fixed_km={k:v.tolist() for k,v in edges.items()},along_width_km=float(np.linalg.norm(edges['along_plus']-edges['along_minus'])),cross_width_km=float(np.linalg.norm(edges['cross_plus']-edges['cross_minus'])),mean_height_flat_cross_width_km=2*cfg['constellation']['h_km']*math.tan(math.radians(cam['cross_half_deg'])),geocentric_vs_geodetic_normal_deg=math.degrees(math.acos(np.clip(np.dot(normal,radial),-1,1))),model='Ellipsoid ray intersections; widths are straight ECEF endpoint distances, not geodesic surface arc lengths')
        write(folder/'footprint.json',footprint)
        check('footprint_anisotropy',footprint['cross_width_km']>400*footprint['along_width_km'],cross_width_km=footprint['cross_width_km'],along_width_km=footprint['along_width_km'])
        write(folder/'trajectory_frames.json',dict(run_id=ctx['run_id'],samples=list(samples.values())))
        csv_write(folder/'trajectory.csv',[dict(time_epsec=t,**{f'{kind}_{axis}':s[k][i] for kind,k in [('j2000_km','j2000_position_km'),('fixed_km','fixed_position_km'),('j2000_km_s','j2000_velocity_km_s')] for i,axis in enumerate('xyz')}) for t,s in sorted(samples.items())])
        check('basis_and_transform',all(s['transform_error_km']<tol['position_km'] and s['body_axis_error']<tol['axis_vector'] and s['sensor_axis_error']<tol['axis_vector'] and s['orthogonality_error']<tol['orthogonal'] for s in samples.values()),max_body_axis_error=max(s['body_axis_error'] for s in samples.values()),max_sensor_axis_error=max(s['sensor_axis_error'] for s in samples.values()),max_transform_error_km=max(s['transform_error_km'] for s in samples.values()))
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(1,2,figsize=(12,5))
        for p in points:
            delta=targets[p['name']]-center;xx=float(np.dot(delta,basis0[:,0]))*1000;yy=float(np.dot(delta,basis0[:,1]))
            axes[0].scatter(yy,xx);axes[0].annotate(p['name'],(yy,xx),xytext=(3,3),textcoords='offset points',fontsize=8)
        from matplotlib.patches import Rectangle
        axes[0].add_patch(Rectangle((-footprint['cross_width_km']/2,-footprint['along_width_km']*500),footprint['cross_width_km'],footprint['along_width_km']*1000,fill=False,color='black',label='Half-angle footprint extent'))
        axes[0].set(xlabel='Cross-track (km)',ylabel='Along-track (m)',title='Diagnostic projection on ellipsoid')
        times=np.linspace(t0-.012,t0+.012,61);values=[margin(float(t)) for t in times]
        axes[1].plot((times-t0)*1000,values,label='Independent angular margin')
        axes[1].axhline(0,color='black',lw=.7)
        if counts_ok:axes[1].axvspan((convergence[-1]['start_epsec']-t0)*1000,(convergence[-1]['stop_epsec']-t0)*1000,alpha=.2,color='green',label='STK fine Access')
        axes[1].set(xlabel='Time from selected epoch (ms)',ylabel='Inside margin (deg)',title='True positive event and independent geometry');axes[1].legend(fontsize=8)
        for ax in axes:ax.grid(alpha=.2)
        fig.tight_layout();fig.savefig(folder/'geometry_projection.png',dpi=170);plt.close(fig)
        # Include plotting samples as replayable evidence too.
        write(folder/'trajectory_frames.json',dict(run_id=ctx['run_id'],samples=list(samples.values())))
        project=folder/'project';project.mkdir();root.SaveScenarioAs(str(project/'Round2.sc'))
        check('project_saved',(project/'Round2.sc').is_file() and (project/'Sat001.sa').is_file())
        saved=(project/'Sat001.sa').read_text(errors='replace')
        model['propagation_step_readback_s']=float(re.search(r'TimeStep\s+([0-9.eE+-]+)',saved).group(1))
        model['saved_j2']=bool(re.search(r'BEGIN J2Perturbation',saved));model['saved_display_frame']=re.search(r'DisplayCoordSys\s+(\w+)',saved).group(1)
        write(folder/'model_readback.json',model)
        check('propagator_frame_step_readback',model['propagation_step_readback_s']==cfg['propagation_step_s'] and model['saved_j2'] and model['saved_display_frame']==cfg['reference_frame'])
        # Reopen this run's own scenario in its own instance; never close a user scenario.
        root.CloseScenario();root.LoadScenario(str(project/'Round2.sc'))
        check('project_reopened',root.CurrentScenario.Children.Count==1+len(points))
    finally:
        if owned:
            try:
                app.Quit();wait=win32event.WaitForSingleObject(handle,5000);forced=False
                if wait!=0:
                    # This HANDLE refers to the exact new process, not a PID lookup susceptible to reuse.
                    win32api.TerminateProcess(handle,0);forced=True;wait=win32event.WaitForSingleObject(handle,5000)
                cleanup=dict(run_id=ctx['run_id'],pid=pid,quit_requested=True,process_exited=wait==0,forced_termination=forced,ownership_basis='retained handle opened immediately after COM returned a PID absent from preexisting process set; empty scenario verified')
            except Exception as exc:cleanup=dict(run_id=ctx['run_id'],pid=pid,error=repr(exc),forced_termination=False)
        else:cleanup=dict(run_id=ctx['run_id'],owned=False,reason='No verified empty new instance acquired; no cleanup of unowned process')
        write(folder/'cleanup.json',cleanup)
        if handle:handle.Close()
        check('owned_process_cleanup',cleanup.get('process_exited',False),**cleanup)

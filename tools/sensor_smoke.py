"""Optional 1 camera x 3 interior points, real STK Sensor Access; no coverage metrics."""
import argparse,csv,json,math,pathlib,traceback
import win32com.client,win32process
from shapely.geometry import shape,Point
ROOT=pathlib.Path(__file__).resolve().parents[1]
def main(output):
    out=pathlib.Path(output);work=out/'sensor';work.mkdir(exist_ok=True)
    result=dict(status='RUNNING',checks=[],commands=[],intervals=[],queries=[],
                propagation_step_s=30,report_sample_step_s=30,
                access_max_step_s=.01,access_min_step_s=.001,time_convergence_s=.00001,
                assumption='Provisional half angles: XZ along-track 0.001622 deg; YZ cross-track 0.81 deg. +Z geocentric nadir, +X ECI velocity constraint; quaternion 0,0,0,1 identity mounting.',
                caveat='Three points, one camera, two hours only. No event completeness or convergence claim.')
    app=None;owned=False
    def save(): (work/'sensor_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    def cmd(s):result['commands'].append(s);save();print(s,flush=True);return root.ExecuteCommand(s)
    save()
    try:
        before=set(win32process.EnumProcesses());app=win32com.client.DispatchEx('STK11.Application');pid=app.ProcessID
        if pid in before:raise RuntimeError('BLOCKED: existing process returned; not touched')
        root=app.Personality2;root.Isolate()
        if root.CurrentScenario is not None:raise RuntimeError('BLOCKED: nonempty scene; not touched')
        owned=True;result['pid']=pid;result['isolated']=True;save()
        app.Visible=False;app.UserControl=False
        root.LoadScenario(str(out/'stk/project/SmokeRound1.sc'))
        root.UnitPreferences.SetCurrentUnit('DistanceUnit','km');root.UnitPreferences.SetCurrentUnit('AngleUnit','deg');root.UnitPreferences.SetCurrentUnit('DateFormat','EpSec')
        cmd('SetAttitude */Satellite/Sat001 Profile NadirECIVel Offset 0')
        cmd('New / */Satellite/Sat001/Sensor Camera')
        cmd('Define */Satellite/Sat001/Sensor/Camera Rectangular 0.81 0.001622')
        cmd('Point */Satellite/Sat001/Sensor/Camera Fixed Quaternion 0 0 0 1')
        sensor=root.GetObjectFromPath('Satellite/Sat001/Sensor/Camera')
        result['pattern_readback']={'horizontal_half_deg':sensor.Pattern.HorizontalHalfAngle,'vertical_half_deg':sensor.Pattern.VerticalHalfAngle}
        assert abs(result['pattern_readback']['horizontal_half_deg']-.001622)<1e-10
        assert abs(result['pattern_readback']['vertical_half_deg']-.81)<1e-10
        gj=json.loads(next((ROOT/'data').rglob('yangtze_river_delta_boundary.geojson')).read_text(encoding='utf-8'))
        boundary=shape(gj['geometry']);polys=sorted(boundary.geoms,key=lambda x:x.area,reverse=True)
        for i,poly in enumerate(polys):
            point=poly.representative_point();assert boundary.contains(point)
            lon,lat=point.coords[0];name=f'CheckPoint{i+1}'
            cmd('New / Facility '+name)
            facility=root.GetObjectFromPath('Facility/'+name);facility.Position.AssignGeodetic(lat,lon,0)
            query=dict(point=name,longitude_deg=lon,latitude_deg=lat,altitude_km=0,inside_boundary=True,component=i)
            access=sensor.GetAccessToObject(facility)
            access.Advanced.UseFixedTimeStep=False;access.Advanced.UsePreciseEventTimes=True
            access.Advanced.MaxTimeStep=.01;access.Advanced.MinTimeStep=.001;access.Advanced.TimeConvergence=.00001
            access.Advanced.EnableLightTimeDelay=False
            query['settings_readback']={k:getattr(access.Advanced,k) for k in ['MaxTimeStep','MinTimeStep','TimeConvergence','UsePreciseEventTimes','UseFixedTimeStep','EnableLightTimeDelay']}
            access.ComputeAccess()
            intervals=access.ComputedAccessIntervalTimes;query['event_count']=intervals.Count
            if intervals.Count:
                data=access.DataProviders.Item('Access Data').Exec(0,7200)
                starts=data.DataSets.GetDataSetByName('Start Time').GetValues();stops=data.DataSets.GetDataSetByName('Stop Time').GetValues()
                for a,b in zip(starts,stops):
                    a=float(a);b=float(b);assert math.isfinite(a) and math.isfinite(b) and 0<=a<b<=7200
                    result['intervals'].append(dict(sensor='Sat001/Camera',point=name,start_epsec=a,stop_epsec=b,duration_s=b-a))
            # Independent short-window geometry diagnostic from real STK fixed positions.
            sat=root.GetObjectFromPath('Satellite/Sat001')
            data=sat.DataProviders.Item('Cartesian Position').Group.Item('Fixed').ExecElements(0,7200,30,['x','y','z'])
            xyz=list(zip(*[data.DataSets.GetDataSetByName(k).GetValues() for k in ['x','y','z']]))
            ll=math.radians(lon);pp=math.radians(lat);unit=(math.cos(pp)*math.cos(ll),math.cos(pp)*math.sin(ll),math.sin(pp))
            separation=[math.degrees(math.acos(max(-1,min(1,sum(a*b for a,b in zip(pos,unit))/math.sqrt(sum(x*x for x in pos)))))) for pos in xyz]
            query['sampled_min_geocentric_separation_deg']=min(separation)
            query['separation_note']='30 s sample and geodetic latitude used as spherical approximation; diagnostic only, not proof of no access.'
            result['queries'].append(query);save();print(query,flush=True)
        with (work/'sensor_access.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=['sensor','point','start_epsec','stop_epsec','duration_s']);w.writeheader();w.writerows(result['intervals'])
        project=work/'project';project.mkdir(exist_ok=True);root.SaveScenarioAs(str(project/'SensorSmoke.sc'))
        result['status']='PASS';result['checks']=[dict(id='sensor_access',status='PASS',queries=len(result['queries']),events=len(result['intervals']))]
    except Exception as exc:
        traceback.print_exc();result['status']='BLOCKED' if 'BLOCKED:' in str(exc) else 'FAIL'
        result['checks']=[dict(id='sensor_access',status=result['status'],error=repr(exc))]
    finally:
        save()
        if owned:
            try:app.Quit()
            except Exception as exc:result['cleanup_error']=repr(exc);save()
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);raise SystemExit(main(p.parse_args().output))

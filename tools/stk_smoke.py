"""STK 11.6 smoke worker. Only ever controls a newly created empty instance.

Connect syntax is verified against the locally installed STK 11 documentation.
No numeric COM enums, no GetActiveObject, and no closing existing scenarios.
Run under tools/run_smoke.py for bounded wall time.
"""
import argparse, csv, json, math, pathlib, sys, traceback
import win32com.client
import win32process

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.constellation import CONSTANTS,validate

START='1 Jan 2027 00:00:00.000'
STOP='1 Jan 2027 02:00:00.000'

def main(out):
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    evidence={'checks':[], 'instances':[], 'commands':[], 'propagation_step_s':30, 'report_sample_step_s':30,
              'duration_s':7200,'time_system':'UTCG provisional','reference_frame':'J2000'}
    app=None;owned=False;current='stk_connection'
    def save(): (out/'stk_results.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
    def check(id,status,**kw): evidence['checks'].append(dict(id=id,status=status,**kw));save();print(id,status,kw,flush=True)
    def new_instance():
        before=set(win32process.EnumProcesses())
        a=win32com.client.DispatchEx('STK11.Application')
        pid=a.ProcessID
        if pid in before: raise RuntimeError('BLOCKED: COM returned an existing process; no changes made')
        r=a.Personality2
        r.Isolate()
        if r.CurrentScenario is not None: raise RuntimeError('BLOCKED: new process has nonempty scenario; no changes made')
        evidence['instances'].append(dict(pid=pid,absent_from_before=True,empty_scenario=True));save()
        a.Visible=False;a.UserControl=False
        return a,r
    def cmd(command):
        evidence['commands'].append(command);save();print(command,flush=True)
        res=root.ExecuteCommand(command)
        return [res.Item(i) for i in range(res.Count)]
    def sample(sat):
        root.UnitPreferences.SetCurrentUnit('DateFormat','EpSec')
        dp=sat.DataProviders.Item('Cartesian Position').Group.Item('J2000')
        result=dp.ExecElements(0,7200,30,['Time','x','y','z'])
        arrays=[result.DataSets.GetDataSetByName(n).GetValues() for n in ['Time','x','y','z']]
        values=[tuple(map(float,row)) for row in zip(*arrays)]
        assert len(values)==241 and values[0][0]==0 and values[-1][0]==7200
        assert all(all(math.isfinite(x) for x in row) for row in values)
        assert all(b[0]>a[0] for a,b in zip(values,values[1:]))
        assert all(6800<math.sqrt(sum(x*x for x in row[1:]))<7200 for row in values)
        root.UnitPreferences.SetCurrentUnit('DateFormat','UTCG')
        return values
    def export_samples(rows,label):
        path=out/(label+'_positions.csv');count=0
        with path.open('w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f);writer.writerow(['sat_id','time_epsec','x_j2000_km','y_j2000_km','z_j2000_km'])
            for row in rows:
                values=sample(root.GetObjectFromPath('Satellite/'+row['sat_id']))
                writer.writerows((row['sat_id'],*v) for v in values);count+=len(values)
        return count
    def read_elements(rows):
        root.UnitPreferences.SetCurrentUnit('DateFormat','EpSec')
        found=[]
        fields=['Semi-major Axis','Eccentricity','Inclination','RAAN','Arg of Latitude']
        for row in rows:
            sat=root.GetObjectFromPath('Satellite/'+row['sat_id'])
            data=sat.DataProviders.Item('Classical Elements').Group.Item('J2000').ExecSingleElements(0,fields)
            vals={key:float(data.DataSets.GetDataSetByName(key).GetValues()[0]) for key in fields}
            assert abs(vals['Semi-major Axis']-(CONSTANTS['mean_radius_km']+row['h_km']))<1e-5,vals
            assert abs(vals['Eccentricity'])<1e-8,vals
            assert abs(vals['Inclination']-row['i_deg'])<1e-6,vals
            for field,key in [('RAAN','raan_deg'),('Arg of Latitude','u_deg')]:
                assert abs((vals[field]-row[key]+180)%360-180)<1e-5,vals
            found.append(dict(sat_id=row['sat_id'],**vals))
        root.UnitPreferences.SetCurrentUnit('DateFormat','UTCG')
        return found
    try:
        save()
        with (out.parent/'orbits.csv').open(encoding='utf-8') as f:
            rows=[{k:(v if k=='sat_id' else float(v)) for k,v in row.items()} for row in csv.DictReader(f)]
        validate(rows,28)
        app,root=new_instance();owned=True
        evidence['version']=cmd('GetSTKVersion / Details');evidence['platform']=cmd('GetSTKVersion / Platform')
        evidence['licenses']=cmd('GetLicenses /')
        root.NewScenario('SmokeRound1_'+out.parent.name[-15:].replace('-','_'))
        root.UnitPreferences.SetCurrentUnit('DateFormat','UTCG')
        root.UnitPreferences.SetCurrentUnit('DistanceUnit','km')
        root.UnitPreferences.SetCurrentUnit('AngleUnit','deg')
        root.CurrentScenario.SetTimePeriod(START,STOP)
        root.CurrentScenario.Epoch=START
        cmd('Units_Set * Connect Distance km Date GregorianUTC Time sec')
        check(current,'PASS',instances=evidence['instances'])
        current='stk_1sat'
        for idx,row in enumerate(rows):
            satpath='*/Satellite/'+row['sat_id']
            cmd('New / Satellite '+row['sat_id'])
            cmd(f'SetState {satpath} Classical J2Perturbation "{START}" "{STOP}" 30 J2000 "{START}" {CONSTANTS["mean_radius_km"]+row["h_km"]:.12f} 0 {row["i_deg"]:.12f} 0 {row["raan_deg"]:.12f} {row["u_deg"]:.12f}')
            if idx==0:
                count=export_samples(rows[:1],'diagnostic1')
                evidence['diagnostic_elements']=read_elements(rows[:1])
                check(current,'PASS',samples=count,diagnostic_not_formal_n=True)
                current='stk_28sat'
        count=export_samples(rows,'baseline28')
        evidence['initial_elements']=read_elements(rows)
        assert root.CurrentScenario.Children.Count==28
        check(current,'PASS',satellites=28,samples=count)
        current='stk_save'
        project=out/'project';project.mkdir(exist_ok=True)
        scenario=project/'SmokeRound1.sc'
        root.SaveScenarioAs(str(scenario))
        check(current,'PASS',scenario=str(scenario),files=len(list(project.iterdir())))
        app.Quit();app=None;owned=False
        current='stk_reopen'
        app,root=new_instance();owned=True
        root.LoadScenario(str(scenario))
        root.UnitPreferences.SetCurrentUnit('DateFormat','UTCG')
        root.UnitPreferences.SetCurrentUnit('DistanceUnit','km');root.UnitPreferences.SetCurrentUnit('AngleUnit','deg')
        assert root.CurrentScenario.Children.Count==28
        evidence['reopened_period']=[str(root.CurrentScenario.StartTime),str(root.CurrentScenario.StopTime)]
        assert root.ConversionUtility.ConvertDate('UTCG','EpSec',root.CurrentScenario.StartTime)=='0.000000' or float(root.ConversionUtility.ConvertDate('UTCG','EpSec',root.CurrentScenario.StartTime))==0
        assert float(root.ConversionUtility.ConvertDate('UTCG','EpSec',root.CurrentScenario.StopTime))==7200
        evidence['reopened_elements']=read_elements(rows)
        assert evidence['reopened_elements']==evidence['initial_elements']
        count=export_samples(rows[:1],'reopened1')
        assert (out/'reopened1_positions.csv').read_bytes()==(out/'diagnostic1_positions.csv').read_bytes()
        check(current,'PASS',satellites=28,sample_rows=count)
    except Exception as exc:
        traceback.print_exc()
        check(current,'BLOCKED' if current=='stk_connection' or 'BLOCKED:' in str(exc) else 'FAIL',error=repr(exc))
    finally:
        if app is not None and owned:
            try: app.Quit()
            except Exception as exc: evidence['cleanup_error']=repr(exc)
        for name in ['stk_connection','stk_1sat','stk_28sat','stk_save','stk_reopen']:
            if not any(x['id']==name for x in evidence['checks']):check(name,'BLOCKED',reason='prior STK stage did not complete')
        save()
    return 0 if all(x['status']=='PASS' for x in evidence['checks']) else 1
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True)
    raise SystemExit(main(p.parse_args().output))

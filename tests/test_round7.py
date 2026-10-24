import copy,tempfile,unittest
from pathlib import Path
import numpy as np
from types import SimpleNamespace
from src.round2.evidence import ROOT,read,KEYS
from src.round6.report import LABELS,phenomena_text
from src.round7.candidates import coefficients,cell_windows,normalize_sources
from src.round7.mechanisms import run_mechanisms
from src.round7.integrity import audit_ledger


class Round7Tests(unittest.TestCase):
    def test_shared_camera_requires_every_satellite_binding_and_constraints(self):
        from src.round7.audit import audit_constraints
        with tempfile.TemporaryDirectory() as d:
            current=Path(d)/'current';old=Path(d)/'old';current.mkdir();old.mkdir()
            constraint='BEGIN AccessConstraints\nLineOfSight On\nEND AccessConstraints\n'
            sat=constraint+'BEGIN SubObjects\nClass Sensor\nCamera\nEND Class\nEND SubObjects\n'
            for base in [current,old]:
                for name in ['Sat001','Sat002']:(base/(name+'.sa')).write_text(sat,encoding='utf-8')
                (base/'Camera.sn').write_text(constraint,encoding='utf-8');(base/'P.f').write_text(constraint,encoding='utf-8')
            audit=lambda:audit_constraints(current,old,['Sat001','Sat002'],['P'])
            self.assertEqual(audit()['status'],'PASS');self.assertEqual(audit()['unique_sensor_files'],1)
            (current/'Sat002.sa').write_text(sat.replace('Camera','Other'),encoding='utf-8');self.assertEqual(audit()['status'],'FAIL')
            (current/'Sat002.sa').write_text(sat,encoding='utf-8');(current/'Camera.sn').write_text(constraint.replace('On','Off'),encoding='utf-8');self.assertEqual(audit()['status'],'FAIL')
            (current/'Camera.sn').write_text(constraint,encoding='utf-8');(current/'P.f').unlink();self.assertEqual(audit()['status'],'FAIL')

    def test_memory_forecast_separates_fixed_cost_conservatively(self):
        from src.round7.performance import memory_forecast
        mb=1024*1024
        r=memory_forecast(650*mb,850*mb,125*mb)
        self.assertEqual(r['fixed_baseline_credit_bytes'],522*mb)
        self.assertEqual(r['projected_combined_bytes'],(522+7*328+7*125)*mb)
        self.assertEqual(r['guarded_combined_bytes'],1.5*r['projected_combined_bytes'])
        self.assertGreater(r['projected_stk_bytes'],850*mb)
        # Tiny/unknown fixed baseline earns no credit; a larger empty peak
        # cannot yield negative variable allocation.
        self.assertEqual(memory_forecast(0,850*mb,125*mb)['projected_combined_bytes'],7*(850+125)*mb)
        self.assertGreaterEqual(memory_forecast(950*mb,850*mb,125*mb)['projected_stk_bytes'],850*mb)

    def test_current_provider_grid_and_eviction_never_overwrite(self):
        from src.round7.production import Model
        cfg=read(ROOT/'configs/round7.json');ctx={k:k for k in KEYS}
        for shifted in [False,True]:
            requests=[]
            def provider(name):
                def execute(times,elements):
                    requests.append(list(times));actual=list(times)
                    if shifted:actual[2]-=.371
                    xyz=[7000.,0.,0.] if name=='Cartesian Position' else [0.,7.,0.]
                    data={'Time':actual,**{k:[xyz[i]]*len(times) for i,k in enumerate('xyz')}}
                    return SimpleNamespace(Valid=True,Count=4,GetArray=lambda k:data[k])
                return SimpleNamespace(Group=SimpleNamespace(Item=lambda _:SimpleNamespace(ExecSingleElementsArray=execute)))
            with tempfile.TemporaryDirectory() as d:
                m=Model.__new__(Model);m.coarse_cache={};m.calls=0;m.cfg=cfg;m.ctx=ctx;m.trajectory_sequence=0
                m.profile={k:0 for k in ['provider_requests','provider_states','bulk_provider_s','basis_s','trajectory_write_s']}
                m.sat=SimpleNamespace(DataProviders=SimpleNamespace(Item=provider));m.sat_id='Sat001';m.folder=Path(d)
                m.state=lambda _:(np.array([7000.,0.,0.]),np.array([[0.,0.,-1.],[1.,0.,0.],[0.,-1.,0.]]))
                if shifted:
                    with self.assertRaisesRegex(ValueError,'grid mismatch'):m.coarse_states(10.,16.2,2.,.371)
                else:
                    result=m.coarse_states(10.,16.2,2.,.371)
                    np.testing.assert_allclose(result[0],[10.,10.371,12.371,14.371,16.2],rtol=0,atol=1e-12)
                    original=Path(result[3]['path']).read_bytes();m.coarse_cache.clear();again=m.coarse_states(10.,16.2,2.,.371)
                    self.assertNotEqual(result[3]['path'],again[3]['path']);self.assertEqual(Path(result[3]['path']).read_bytes(),original)
                    self.assertEqual(m.profile['provider_requests'],4);self.assertEqual(m.profile['provider_states'],20)

    def test_report_mixed_states_and_inverse(self):
        for invert in [False,True]:
            states={k:dict(observed=(i<2)^invert) for i,k in enumerate(LABELS)}
            text=phenomena_text(states)
            for k,v in states.items():
                line=next(x for x in text.splitlines() if k in x)
                self.assertIn('；true' if v['observed'] else '；false',line)
                self.assertIn('已观测到' if v['observed'] else '未观测到',line)
        self.assertEqual(phenomena_text({}).count('；null'),4)

    def test_cubic_exact_nonuniform_grid(self):
        t=np.array([0.,.371,2.371,4.371,5.]);v=np.column_stack([t**3-3*t*t+2,t*t,t*0+600])
        p=coefficients(t,v)
        for i in range(len(t)-1):
            for u in [.1,.4,.8]:
                x=t[i]+u*(t[i+1]-t[i]);expected=[x**3-3*x*x+2,x*x,600]
                np.testing.assert_allclose(sum(p[i,k]*u**k for k in range(4)),expected,rtol=0,atol=1e-10)

    def test_unresolved_not_zero(self):
        with self.assertRaisesRegex(ValueError,'UNRESOLVED'):
            coefficients([0.,1.,2.],np.ones((3,3)))

    def test_distinct_candidate_windows_never_distance_dedup(self):
        rows=[dict(possible_intervals=[[1.,1.001]]),dict(possible_intervals=[[1.005,1.006]])]
        self.assertEqual(len(normalize_sources(rows,.0001,10)),2)
        self.assertEqual(normalize_sources(rows,.01,10)[0]['source_indices'],[0,1])

    def test_all_analytic_counterexamples_on_production(self):
        cfg=read(ROOT/'configs/round7.json');ctx={k:k for k in KEYS}
        with tempfile.TemporaryDirectory() as d:
            result=run_mechanisms(ctx,cfg,Path(d))
            self.assertEqual(result['status'],'PASS',result)
            folder=Path(d)/'negative_empty_query'
            o=read(folder/'opportunities.json');c=read(folder/'candidate_ledger.json');q=read(folder/'raw_access_queries.json');r=read(folder/'raw_intervals.json')
            audit=lambda oo,cc,qq,rr:audit_ledger(ctx,'CONSTRUCTED',oo,cc,qq,rr,'MECHANISM_ONLY',.05,15)
            self.assertEqual(audit(o,c,q,r)['status'],'PASS')
            bad=audit(o,c,[],r);self.assertEqual(bad['status'],'FAIL');self.assertTrue(any('MISSING_CANDIDATE_QUERY' in e for e in bad['errors']))
            self.assertEqual(audit(o,[],[],r)['status'],'FAIL')
            bad=copy.deepcopy(q);bad[0]['query_status']='FAILED';self.assertEqual(audit(o,c,bad,r)['status'],'FAIL')
            bad=copy.deepcopy(q);bad[0]['code_sha256']='other';self.assertEqual(audit(o,c,bad,r)['status'],'FAIL')
            self.assertEqual(audit(o,c,q+q,r)['status'],'FAIL')

    def test_round7_contract_is_distinct_from_round6(self):
        from src.round7.audit import validate_config
        from src.round6.audit import validate_config as old
        cfg=read(ROOT/'configs/round7.json');self.assertTrue(validate_config(cfg))
        with self.assertRaises(ValueError):old(cfg)
        for field,value in [('satellites',cfg['round7']['satellites'][::4]),('window',[0,86400]),('candidate_guard_km',.1)]:
            bad=copy.deepcopy(cfg);bad['round7'][field]=value
            with self.assertRaises(ValueError):validate_config(bad)

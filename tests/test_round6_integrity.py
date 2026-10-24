import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from src.round2.evidence import read
from src.round6.integrity import audit_ledger,audit_trajectory,expected_times
from src.round6.audit import validate_config
from src.round6.production import search_batch,Model


class Round6Tests(unittest.TestCase):
    def setUp(self):
        self.cfg=read(Path(__file__).resolve().parents[1]/'configs/round6.json')
        self.ctx=dict(run_id='test',config_sha256='cfg',code_sha256='code',input_sha256='input')
        self.ident=dict(self.ctx,point_set_sha256='points',sensor_id='Camera')

    def ledger(self):
        opp=[];cs=[];qs=[]
        # Two distinct zero-event queries must BOTH be required.
        for i,t in enumerate([5.,15.]):
            cid=f'C{i}';qid=f'Q{i}';sid=f'S{i}';local=[t-.05,t+.05]
            source=dict(source_id=sid,candidate_id=cid,query_id=qid,reason_code='REFINE_SENSOR_ACCESS',exact_root_epsec=t,coarse_bracket=[t-1,t+1])
            o=dict(self.ident,sat_id='Sat001',point_id=f'P{i}',evaluation_window=[0.,20.],candidates=[source]);opp.append(o)
            c=dict(self.ident,candidate_id=cid,query_id=qid,sat_id='Sat001',point_id=f'P{i}',evaluation_window=[0.,20.],
                candidate_root_epsec=t,coarse_bracket=[t-1,t+1],source_ids=[sid],local_query_window=local,event_count=0,query_status='COMPLETE');cs.append(c)
            q=dict(self.ident,candidate_id=cid,query_id=qid,sat_id='Sat001',point_id=f'P{i}',evaluation_window=[0.,20.],window=local,
                candidate_root_epsec=t,query_type='MAIN_FINE',query_status='COMPLETE',event_count=0,raw_event_count=0,intervals=[],unclipped_intervals=[]);qs.append(q)
        return opp,cs,qs,[]

    def audit(self,parts):return audit_ledger(self.ctx,'points',*parts,'MAIN_FINE',.05,604800.)

    def test_complete_empty_ledger_passes_then_each_missing_query_fails_by_candidate(self):
        parts=self.ledger();self.assertEqual(self.audit(parts)['status'],'PASS')
        for index in range(2):
            mutated=copy.deepcopy(parts);mutated[2].pop(index);result=self.audit(mutated)
            self.assertEqual(result['status'],'FAIL');self.assertIn('MISSING_CANDIDATE_QUERY:C'+str(index),result['errors'])

    def test_orphan_and_duplicate_queries_fail(self):
        for mode in ['orphan','duplicate_id','duplicate_candidate']:
            parts=self.ledger();self.assertEqual(self.audit(parts)['status'],'PASS')
            q=copy.deepcopy(parts[2][0])
            if mode=='orphan':q.update(query_id='other',candidate_id='unknown')
            if mode=='duplicate_candidate':q['query_id']='other'
            parts[2].append(q)
            self.assertEqual(self.audit(parts)['status'],'FAIL',mode)

    def test_query_wrong_identity_point_satellite_windows_status_and_type_fail(self):
        for key,value in [('run_id','old'),('code_sha256','old'),('sat_id','Sat005'),('point_id','bad'),
            ('window',[4,6]),('evaluation_window',[0,172800]),('candidate_root_epsec',10),
            ('query_status','FAILED'),('query_status','TIMEOUT'),('query_type','MEDIUM_CONVERGENCE')]:
            parts=self.ledger();self.assertEqual(self.audit(parts)['status'],'PASS');parts[2][0][key]=value
            with self.subTest(key=key,value=value):self.assertEqual(self.audit(parts)['status'],'FAIL')

    def test_removing_normalized_candidate_does_not_remove_its_obligation(self):
        parts=self.ledger();parts[1].pop();self.assertIn('CANDIDATE_LEDGER_MISMATCH',self.audit(parts)['errors'])

    def test_reverse_query_and_dedup_source_mapping_required(self):
        for key,value in [('query_id','other'),('source_ids',[])]:
            parts=self.ledger();parts[1][0][key]=value;self.assertEqual(self.audit(parts)['status'],'FAIL')

    def trajectory(self,window=[0.,20.],step=5.,phase=0.):
        times=expected_times(window,step,phase,1e-8).tolist()
        return dict(self.ctx,sat_id='Sat001',window=window,step_s=step,phase_s=phase,times_epsec=times,
            positions_fixed_km=[[1.,2.,3.] for _ in times],bases_screening=[np.eye(3).tolist() for _ in times])

    def check_traj(self,data,window=[0.,20.],step=5.,phase=0.):
        return audit_trajectory(data,self.ctx,'Sat001',window,step,phase)

    def test_seven_day_expected_count(self):
        times=expected_times([0.,604800.],5.,0.,1e-8)
        self.assertEqual(len(times),120961);self.assertTrue(np.all(np.diff(times)==5))

    def test_phase_and_nondivisible_windows_include_short_endpoint_segments(self):
        for window,step,phase in [([1.219,124.537],1.,.619),([596.287,733.431],2.,.371),([10.,10.2],5.,.371)]:
            with self.subTest(window=window):
                data=self.trajectory(window,step,phase);self.assertEqual(self.check_traj(data,window,step,phase)['status'],'PASS')
                self.assertEqual(data['times_epsec'][0],window[0]);self.assertEqual(data['times_epsec'][-1],window[-1])

    def test_trajectory_missing_first_last_middle_and_wrong_count_rejected(self):
        for index in [0,-1,2]:
            data=self.trajectory();self.assertEqual(self.check_traj(data)['status'],'PASS')
            for key in ['times_epsec','positions_fixed_km','bases_screening']:data[key].pop(index)
            result=self.check_traj(data)
            self.assertEqual(result['status'],'FAIL');self.assertIn('TRAJECTORY_COUNT',result['errors'])

    def test_trajectory_reverse_duplicate_phase_nonfinite_and_dimensions_rejected(self):
        for mode in ['reverse','duplicate','phase','nonfinite','positions','bases','identity']:
            data=self.trajectory();self.assertEqual(self.check_traj(data)['status'],'PASS')
            if mode=='reverse':data['times_epsec'].reverse()
            if mode=='duplicate':data['times_epsec'][2]=data['times_epsec'][1]
            if mode=='phase':data['times_epsec'][2]+=.371
            if mode=='nonfinite':data['times_epsec'][2]=float('nan')
            if mode=='positions':data['positions_fixed_km'].pop()
            if mode=='bases':data['bases_screening'][0]=[1,2,3]
            if mode=='identity':data['sat_id']='Sat005'
            self.assertEqual(self.check_traj(data)['status'],'FAIL',mode)

    def test_round6_contract_rejects_duration_and_sampling_drift(self):
        self.assertTrue(validate_config(self.cfg))
        for mode in ['duration','phase','tier','walker','stop']:
            cfg=copy.deepcopy(self.cfg)
            if mode=='duration':cfg['duration_s']=172800
            if mode=='phase':cfg['round6']['phase_s']=.371
            if mode=='tier':cfg['round4']['tiers'][0]['id']='T5'
            if mode=='walker':cfg['constellation']['n']=7
            if mode=='stop':cfg['round6']['early_stop']=True
            with self.assertRaises(ValueError):validate_config(cfg)

    def test_provider_receives_explicit_phases_and_shifted_return_is_rejected(self):
        from types import SimpleNamespace
        for shifted in [False,True]:
            requests=[]
            class Provider:
                def ExecSingleElementsArray(self,times,elements):
                    requests.append(times)
                    actual=list(times)
                    if shifted:actual[2]-=.371
                    arrays={'Time':actual,**{k:[1.]*len(times) for k in 'xyz'}}
                    return SimpleNamespace(Valid=True,Count=4,GetArray=lambda k:arrays[k])
            provider=Provider();group=SimpleNamespace(Item=lambda _:provider)
            sat=SimpleNamespace(DataProviders=SimpleNamespace(Item=lambda _:SimpleNamespace(Group=group)))
            with tempfile.TemporaryDirectory() as folder,patch('src.round6.production.body_basis',return_value=np.eye(3)):
                model=Model.__new__(Model);model.coarse_cache={};model.calls=0;model.cfg=self.cfg;model.ctx=self.ctx
                model.sat=sat;model.sat_id='Sat001';model.folder=Path(folder);model.state=lambda _:(np.ones(3),np.eye(3))
                if shifted:
                    with self.assertRaisesRegex(ValueError,'did not honor'):model.coarse_states(10.,16.2,2.,.371)
                else:
                    result=model.coarse_states(10.,16.2,2.,.371)
                    np.testing.assert_allclose(result[0],[10.,10.371,12.371,14.371,16.2],rtol=0,atol=1e-12)
                    self.assertEqual(requests[0],requests[1])

    def test_shared_pipeline_pads_center_outside_start_and_preserves_unclipped_interval(self):
        cfg=self.cfg;ctx=self.ctx
        class FakeModel:
            sat_id='Sat001';access_calls=0
            def access(self,fac,window):
                self.access_calls+=1
                return dict(window=window,intervals=[dict(start_epsec=10-.002,stop_epsec=10+.002,duration_s=.004)],event_count=1)
        model=FakeModel();model.cfg=cfg;model.ctx=ctx
        fake_screen=dict(candidates=[dict(reason_code='REFINE_SENSOR_ACCESS',exact_root_epsec=10.,coarse_bracket=[9.9,10.1])],refinement_candidates=1)
        with tempfile.TemporaryDirectory() as folder,patch('src.round6.production.screen',return_value=fake_screen) as screen,patch('src.round6.production.audit_geometry',return_value=dict(status='PASS',medium_access={})):
            result=search_batch([model],[('P',None,None)],[10.001,20.],5.,0.,folder,'points','unit','DIAGNOSTIC_FINE')
            self.assertEqual(screen.call_args.args[2],[9.901,20.1])
            event=result['raw'][0];self.assertEqual(event['start_epsec'],10.001);self.assertTrue(event['clipped_start'])
            self.assertEqual(result['queries'][0]['unclipped_intervals'][0]['start_epsec'],9.998)
            self.assertEqual(audit_ledger(ctx,'points',result['opportunities'],result['candidates'],result['queries'],result['raw'],'DIAGNOSTIC_FINE',.05,604800.)['status'],'PASS')


if __name__=='__main__':unittest.main(verbosity=2)

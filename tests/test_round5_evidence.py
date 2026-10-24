"""Constructed tests exercise gates only; none are real STK acceptance evidence."""
import copy
import unittest
from unittest.mock import patch
from src.round2.evidence import ROOT,read
from src.round3.statistics import aggregate
from src.round4.acceptance import evaluate,manual_audit
from src.round4.experiments import parameter_check
from src.round4.frozen import EXPECTED
from src.round5.audit import validate_config,validate_events,history_comparison


class Round5Tests(unittest.TestCase):
    def setUp(self):
        self.cfg=read(ROOT/'configs/round5.json');self.ctx=dict(run_id='synthetic',config_sha256='synthetic')
        self.points=[dict(point_id='p'),dict(point_id='q')]
    def record(self,rid='one',sat='Sat001',point='p',a=86000.,b=86000.005):
        return dict(record_id=rid,run_id='synthetic',config_sha256='synthetic',point_set_sha256=EXPECTED,sat_id=sat,
            sensor_id='Camera',point_id=point,query_id=rid,window=[0.,172800.],local_refinement_window=[a-.05,b+.05],
            start_epsec=a,stop_epsec=b,duration_s=b-a,event_status='REAL_SENSOR_ACCESS')
    def evidence(self,raw):
        queries=[];geom=[]
        for r in raw:
            ids={k:r[k] for k in ['run_id','config_sha256','point_set_sha256','sat_id','sensor_id','point_id']}
            interval={k:r[k] for k in ['start_epsec','stop_epsec','duration_s']}
            q=dict(ids,query_id=r['query_id'],window=r['local_refinement_window'],evaluation_window=r['window'],event_count=1,intervals=[interval],
                settings_readback=dict(min_step_s=.001,max_step_s=.001,time_convergence_s=1e-8,fixed_step=False,precise_events=True,light_time=False))
            queries.append(q)
            geom.append(dict(ids,record_id=r['record_id'],status='PASS',event_endpoints=[r['start_epsec'],r['stop_epsec']],
                local_endpoints_not_truncated=True,exact_states=[dict(kind='SYNTHETIC')],angular_reference=dict(intervals=[interval]),
                boundary_comparison=dict(status='PASS'),convergence=dict(status='PASS'),medium_access=dict(intervals=[interval])))
        return queries,geom
    def acceptance(self,raw,queries=None,geom=None):
        q,g=self.evidence(raw);q=q if queries is None else queries;g=g if geom is None else geom
        validation=validate_events(self.ctx,self.cfg,self.points,raw,q,g)
        stats=aggregate(['p','q'],raw,[0,172800]);hand=manual_audit(['p','q'],raw,stats,[0,172800])
        return evaluate(raw,stats,hand,g,validation['status']=='PASS'),stats
    def test_frozen_48_hour_config_rejects_old_duration_and_early_stop(self):
        self.assertTrue(validate_config(self.cfg))
        for mode in ['duration','tier','early','walker']:
            c=copy.deepcopy(self.cfg)
            if mode=='duration':c['duration_s']=86400
            if mode=='tier':c['round4']['tiers'][0]['duration_s']=12000
            if mode=='early':c['round5']['early_stop']=True
            if mode=='walker':c['constellation']['n']=7
            with self.subTest(mode=mode),self.assertRaises(ValueError):validate_config(c)
    def test_h2_and_h3_are_independent(self):
        a=self.record();b=self.record('two','Sat005','q',90000,90000.005)
        h,s=self.acceptance([a,b]);self.assertEqual([h[k]['status'] for k in ['H1','H2','H3']],['PASS','PASS','BLOCKED'])
        b=self.record('two','Sat001','p',90000,90000.005)
        h,s=self.acceptance([a,b]);self.assertEqual([h[k]['status'] for k in ['H1','H2','H3']],['PASS','BLOCKED','PASS'])
    def test_cross_24h_gap_kept_and_edge_gaps_excluded(self):
        h,s=self.acceptance([self.record(),self.record('two','Sat005','p',90000,90000.005)])
        self.assertEqual(s['effective_gap_count'],1);self.assertEqual(s['conditional_mean_gap_s'],90000-86000.005)
        self.assertEqual(h['H3']['status'],'PASS')
    def test_every_geometry_record_must_match(self):
        raw=[self.record(),self.record('two','Sat005','p',90000,90000.005)];q,g=self.evidence(raw)
        for malformed in [g[:1],[dict(g[0]),dict(g[0])],[dict(x,status='FAIL') for x in g],[dict(x,record_id='unknown') for x in g],[dict(status='PASS')]]:
            with self.subTest(geometry=malformed):
                h,_=self.acceptance(raw,q,malformed);self.assertTrue(all(h[k]['status']=='FAIL' for k in ['H1','H2','H3']))
    def test_wrong_identity_query_or_geometry_never_passes(self):
        base=[self.record(),self.record('two','Sat005','p',90000,90000.005)]
        for part,key,value in [('r','run_id','old'),('r','sat_id','Sat002'),('r','point_set_sha256','bad'),('q','config_sha256','old'),('g','point_id','q'),('g','event_endpoints',[1,2]),('g','local_endpoints_not_truncated',False)]:
            raw=copy.deepcopy(base);q,g=self.evidence(raw)
            {'r':raw,'q':q,'g':g}[part][0][key]=value
            with self.subTest(part=part,key=key):
                h,_=self.acceptance(raw,q,g);self.assertTrue(all(h[k]['status']=='FAIL' for k in ['H1','H2','H3']))
    def test_pass_string_cannot_mask_bad_geometry_boundaries(self):
        raw=[self.record()];q,g=self.evidence(raw);g[0]['angular_reference']['intervals']=[dict(start_epsec=5,stop_epsec=6)]
        h,_=self.acceptance(raw,q,g);self.assertEqual(h['H1']['status'],'FAIL')
    def test_adjacent_and_overlap_do_not_create_gaps(self):
        raw=[self.record(a=1,b=2),self.record('two','Sat005','p',2,3),self.record('three','Sat009','p',2.5,4)]
        h,s=self.acceptance(raw);self.assertEqual(s['effective_gap_count'],0);self.assertIsNone(s['conditional_mean_gap_s'])
        self.assertEqual(h['H2']['status'],'PASS');self.assertEqual(h['H3']['status'],'BLOCKED')
        self.assertTrue(h['natural_cross_satellite_union']);self.assertTrue(h['natural_overlap_or_touch'])
    def test_duplicate_same_satellite_event_rejected_without_filtering(self):
        raw=[self.record(),self.record('two')];q,g=self.evidence(raw)
        v=validate_events(self.ctx,self.cfg,self.points,raw,q,g)
        self.assertEqual(v['status'],'FAIL');self.assertEqual(len(v['events']),2)
    def test_nonfinite_or_wrong_duration_rejected(self):
        for key,value in [('duration_s',0),('start_epsec',float('nan'))]:
            raw=[self.record()];q,g=self.evidence(raw);raw[0][key]=value
            with self.subTest(key=key):self.assertEqual(validate_events(self.ctx,self.cfg,self.points,raw,q,g)['status'],'FAIL')
    def test_incomplete_execution_cannot_accept_observed_success(self):
        raw=[self.record(),self.record('two','Sat005','p',90000,90000.005)]
        q,g=self.evidence(raw);s=aggregate(['p','q'],raw,[0,172800]);hand=manual_audit(['p','q'],raw,s,[0,172800])
        h=evaluate(raw,s,hand,g,completed=False);self.assertTrue(all(h[k]['status']=='SKIPPED' for k in ['H1','H2','H3']))
    def test_positive_tiny_gap_not_erased(self):
        raw=[self.record(a=1,b=2),self.record('two','Sat005','p',2.0000001,3)]
        h,s=self.acceptance(raw);self.assertGreater(s['conditional_mean_gap_s'],0)
        self.assertLess(s['conditional_mean_gap_s'],2*self.cfg['tolerances']['independent_event_s'])
    def test_history_comparison_clips_current_second_day(self):
        old=self.record();old['run_id']=self.cfg['round5']['source_round4_run_id']
        current=[self.record(),self.record('two','Sat005','p',90000,90000.005)]
        with patch('src.round5.audit.read',return_value=[old]):
            self.assertEqual(history_comparison(self.cfg,current,self.points)['status'],'PASS')
    def test_propagation_readback_must_cover_48h(self):
        row=dict(h_km=600,i_deg=97.,raan_deg=0,u_deg=0)
        rb=dict(initial_orbit=dict(a_km=self.cfg['earth']['mean_radius_km']+600,e=0,i_deg=97.,arg_perigee_deg=0,raan_deg=0,true_anomaly_deg=0),
            satellite_count=7,epoch_utcg=self.cfg['epoch_utcg'],orbit_epoch_epsec=0,period_epsec=[0,172800],propagation_period_epsec=[0,86400],
            propagator_type=1,step_s=30,attitude_axis_error=0,horizontal_half_deg=.001622,vertical_half_deg=.81,mount_xyzw=[0,0,0,1],points=[])
        self.assertEqual(parameter_check(rb,row,self.cfg,7)['status'],'FAIL')
        rb['propagation_period_epsec']=[0,172800];self.assertEqual(parameter_check(rb,row,self.cfg,7)['status'],'PASS')


if __name__=='__main__':unittest.main(verbosity=2)

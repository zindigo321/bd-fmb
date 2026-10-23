import math
import unittest
from src.round3.statistics import aggregate

def r(i,p,a,b,s='Sat001'):
    return dict(record_id=str(i),point_id=p,start_epsec=a,stop_epsec=b,sat_id=s)

class StatisticsTests(unittest.TestCase):
    def test_overlap_touch_containment_duplicates_disorder_cross_satellite(self):
        rows=[r(1,'p',8,9),r(2,'p',3,5),r(3,'p',1,4),r(4,'p',2,3),
              r(5,'p',5,6,'Sat002'),r(6,'p',1,4,'Sat002')]
        v=aggregate(['p','z'],rows,[0,10]);p=v['points'][0]
        self.assertEqual([(x['start_epsec'],x['stop_epsec']) for x in p['merged_intervals']],[(1,6),(8,9)])
        self.assertEqual(set(p['merged_intervals'][0]['source_record_ids']),{'2','3','4','5','6'})
        self.assertEqual(p['internal_gaps_s'],[2]);self.assertEqual(v['denominator'],2)
        self.assertEqual(v['uncovered_points'],1);self.assertEqual(v['coverage_fraction'],.5)
        self.assertEqual(p['initial_gap_s'],1);self.assertEqual(p['final_gap_s'],1)
    def test_clipping_exclusion_and_endpoint_gaps(self):
        v=aggregate(['p'],[r(1,'p',-2,1),r(2,'p',8,12),r(3,'p',12,14)],[0,10])
        p=v['points'][0];self.assertEqual(p['internal_gaps_s'],[7])
        self.assertEqual(p['initial_gap_s'],0);self.assertEqual(p['final_gap_s'],0)
        self.assertTrue(p['merged_intervals'][0]['clipped_start']);self.assertTrue(p['merged_intervals'][1]['clipped_stop'])
        self.assertEqual(v['excluded_records'],[{'record_id':'3','reason':'OUTSIDE_WINDOW'}])
    def test_zero_one_two_accesses_and_null_thresholds(self):
        v=aggregate(['zero','once','twice'],[r(1,'once',1,2),r(2,'twice',0,1),r(3,'twice',4,5)],[0,10])
        self.assertEqual(v['uncovered_points'],1);self.assertEqual(v['insufficient_access_points'],2)
        self.assertEqual(v['effective_gap_count'],1);self.assertEqual(v['conditional_mean_gap_s'],3)
        self.assertIsNone(v['points'][0]['initial_gap_s']);self.assertIsNone(v['points'][1]['mean_internal_gap_s'])
        self.assertTrue(all(x['elapsed_s'] is None for x in v['coverage_thresholds']))
        z=aggregate(['z'],[],[0,10]);self.assertIsNone(z['conditional_mean_gap_s']);self.assertIsNone(z['conditional_max_gap_s'])
    def test_gap_weighting_is_not_mean_of_point_means(self):
        rows=[r(1,'a',0,1),r(2,'a',2,3),r(3,'a',6,7),r(4,'b',0,1),r(5,'b',10,11)]
        v=aggregate(['a','b','c'],rows,[0,12])
        self.assertEqual(v['effective_gap_count'],3);self.assertAlmostEqual(v['conditional_mean_gap_s'],13/3)
        self.assertEqual(v['conditional_max_gap_s'],9);self.assertEqual(v['insufficient_access_fraction'],1/3)
    def test_invalid_and_nonfinite_data_rejected(self):
        for a,b in [(1,1),(2,1),(float('nan'),2),(1,float('inf'))]:
            with self.subTest(a=a,b=b),self.assertRaises(ValueError):aggregate(['p'],[r(1,'p',a,b)],[0,10])
        for ids,rows,window in [(['p','p'],[],[0,10]),([],[],[0,10]),(['p'],[r(1,'x',1,2)],[0,10]),(['p'],[],[0,math.inf]),(['p'],[r(1,'p',1,2),r(1,'p',2,3)],[0,10])]:
            with self.assertRaises(ValueError):aggregate(ids,rows,window)
    def test_real_short_gap_not_erased_and_threshold_time(self):
        v=aggregate(['p'],[r(1,'p',1,2),r(2,'p',2.0000001,3)],[0,10])
        self.assertEqual(v['points'][0]['merged_access_count'],2)
        self.assertGreater(v['conditional_mean_gap_s'],0)
        self.assertEqual([x['elapsed_s'] for x in v['coverage_thresholds']],[1,1])

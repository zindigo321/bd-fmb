import unittest
from src.round3.experiments import compare

class MatchingTests(unittest.TestCase):
    def test_one_to_one_duplicates_and_missing(self):
        ref=[dict(start_epsec=1,stop_epsec=1.004)]
        self.assertEqual(compare(ref,ref,[0,10],.0002)['status'],'PASS')
        self.assertEqual(compare(ref,ref*2,[0,10],.0002)['extra'],[1])
        self.assertEqual(compare(ref,[],[0,10],.0002)['missed'],[0])
    def test_clipped_endpoints_and_disjoint_window(self):
        ref=[dict(start_epsec=1,stop_epsec=1.004)]
        value=compare(ref,[dict(start_epsec=1.001,stop_epsec=1.004)],[1.001,10],.0002)
        self.assertEqual(value['status'],'PASS');self.assertTrue(value['matches'][0]['clipped_start'])
        self.assertEqual(compare(ref,[],[2,10],.0002)['reference_count'],0)
    def test_boundary_error_and_extra_negative(self):
        ref=[dict(start_epsec=1,stop_epsec=1.004)]
        self.assertEqual(compare(ref,[dict(start_epsec=1.001,stop_epsec=1.004)],[0,10],.0002)['status'],'FAIL')
        self.assertEqual(compare([],ref,[0,10],.0002)['status'],'FAIL')

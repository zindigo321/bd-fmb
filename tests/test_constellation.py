import copy, math, unittest
from src.constellation import generate, validate

class WalkerTests(unittest.TestCase):
    def test_baseline_and_formula(self):
        rows,c=generate(); self.assertEqual(len(rows),28); self.assertEqual(len({r['sat_id'] for r in rows}),28)
        self.assertEqual(rows[4]['raan_deg'],360/7);self.assertAlmostEqual(rows[4]['u_deg'],360/28)
        self.assertEqual(sorted({r['raan_deg'] for r in rows}),c['plane_raan_deg'])
        self.assertEqual(generate(),generate())
    def test_normal_inputs_and_independent_residual(self):
        for n,p,f,h in [(2,1,0,500),(28,7,0,600),(30,6,5,700),(100,10,9,550),(7,1,0,650)]:
            rows,c=generate(n,p,f,h,-30,720)
            self.assertEqual(len(rows),n)
            a=6371.0088+h
            rate=-1.5*1.08262668e-3*(398600.4418/a**3)**.5*(6378.137/a)**2*math.cos(math.radians(rows[0]['i_deg']))
            self.assertLess(abs(rate-2*math.pi/(365.2421897*86400)),1e-15)
            self.assertEqual(validate(rows,n)['count'],n)
        self.assertNotEqual(generate(h_km=500)[0][0]['i_deg'],generate(h_km=700)[0][0]['i_deg'])
    def test_invalid_inputs(self):
        for kw in [dict(n=1),dict(n=101),dict(n=True),dict(n=2.5),dict(n=29),dict(planes=0),dict(planes=8),dict(phase_factor=-1),dict(phase_factor=7),dict(h_km=499),dict(h_km=701),dict(h_km=float('nan')),dict(u0_deg=float('inf'))]:
            with self.subTest(kw=kw),self.assertRaises(ValueError): generate(**kw)
    def test_validator_rejects_corruption(self):
        rows,_=generate()
        for key,value in [('i_deg',98.0),('h_km',499),('raan_deg',360),('u_deg',-1),('h_km',float('nan'))]:
            bad=copy.deepcopy(rows);bad[0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError): validate(bad,28)
        with self.assertRaises(ValueError):validate(rows[:-1],28)
        bad=copy.deepcopy(rows);bad[1]['sat_id']=bad[0]['sat_id']
        with self.assertRaises(ValueError):validate(bad,28)
if __name__=='__main__':unittest.main(verbosity=2)

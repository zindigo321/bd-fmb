import math
import unittest
from src.round3.refine import root

class RefinementTests(unittest.TestCase):
    def test_linear_nonlinear_and_piecewise_boundaries(self):
        self.assertAlmostEqual(root(lambda t:3*t-1,0,1),1/3,places=9)
        self.assertAlmostEqual(root(lambda t:t*t-2,1,2),math.sqrt(2),places=9)
        self.assertAlmostEqual(root(lambda t:min(.01-abs(t),.003-t),0,.02),.003,places=9)
    def test_unbracketed_nonfinite_fail(self):
        with self.assertRaises(ValueError):root(lambda t:t*t+1,-1,1)
        with self.assertRaises(ValueError):root(lambda t:float('nan'),0,1)

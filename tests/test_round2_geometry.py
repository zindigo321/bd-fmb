import unittest
import numpy as np
from src.round2.geometry import ray_ellipsoid,ecef_to_geodetic,geodetic_to_ecef,body_basis,bisect_crossing
class GeometryTests(unittest.TestCase):
    def test_geodetic_roundtrip_and_non_normal_nadir(self):
        a,b=6378.137,6356.75231424
        for lat,lon,h in [(0,0,0),(45,120,0),(-30,-70,2)]:
            p=geodetic_to_ecef(lat,lon,h,a,b);self.assertTrue(np.allclose(ecef_to_geodetic(p,a,b),[lat,lon,h],atol=1e-8))
        p=np.array([7000.,0,1000.]);hit=ray_ellipsoid(p,-p,a,b)
        self.assertAlmostEqual((hit[0]/a)**2+(hit[1]/a)**2+(hit[2]/b)**2,1)
        with self.assertRaises(ValueError):ray_ellipsoid(p,p,a,b)
    def test_basis_orientation_and_root_brackets(self):
        basis=body_basis(np.array([7000.,0,0]),np.array([0.,7.,0]));self.assertTrue(np.allclose(basis[:,2],[-1,0,0]));self.assertTrue(np.allclose(basis[:,0],[0,1,0]));self.assertAlmostEqual(np.linalg.det(basis),1)
        self.assertAlmostEqual(bisect_crossing(lambda x:x*x-2,1,2),2**.5,places=8)
        with self.assertRaises(ValueError):bisect_crossing(lambda x:x*x+1,-1,1)

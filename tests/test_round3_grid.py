import tempfile
import unittest
from pathlib import Path
from src.round2.evidence import ROOT,read
from src.round3.grid import freeze

class GridTests(unittest.TestCase):
    def test_real_boundary_freeze_is_deterministic_and_keeps_components(self):
        cfg=read(ROOT/'configs/round3.json')
        with tempfile.TemporaryDirectory(dir=ROOT/'work') as d:
            a=freeze(ROOT/cfg['input_boundary'],cfg['round3']['grid'],Path(d)/'a','one')
            b=freeze(ROOT/cfg['input_boundary'],cfg['round3']['grid'],Path(d)/'b','two')
            self.assertEqual(a['point_set_sha256'],b['point_set_sha256'])
            self.assertEqual(len(a['points']),16)
            self.assertEqual(len({p['point_id'] for p in a['points']}),16)
            self.assertEqual(a['component_count'],3)
            self.assertGreater(a['full_grid_count'],16)
            self.assertTrue(all(p['altitude_km']==0 and not p['use_terrain'] for p in a['points']))

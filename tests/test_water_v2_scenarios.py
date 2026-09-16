import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from water_v2.engine import compile_scene
from water_v2.model import Settings, load_product
from water_v2.scenarios import SCENARIOS, run_scenarios


class ScenarioEvidenceTests(unittest.TestCase):
    def compiler(self, terrain_path, source_path, bounds, **kwargs):
        x0, x1, z0, z1 = bounds
        terrain = np.tile((100-np.arange(x1-x0)//3).astype(np.int16),(z1-z0,1))
        body = np.zeros(terrain.shape, dtype=bool)
        # Deliberately impossible mapped ridge in one of the real case slots.
        path = np.asarray([[x0+x, z0+192] for x in range(180,204)], dtype=np.int32)
        if x0 == SCENARIOS['hill_junction'][0]-192:
            terrain[192,180:204] = 120
        product, report = compile_scene(terrain, body, [path], origin=(x0,z0))
        return product, report, Settings(), {'fixture': 'ridge'}

    def test_all_locations_are_saved_and_a_failure_cannot_be_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'candidate'
            summary = run_scenarios(directory, 'unused', 'unused', self.compiler)
            self.assertTrue(summary['complete'])
            self.assertFalse(summary['compile_gate_passed'])
            self.assertFalse(summary['world_written'])
            self.assertEqual(set(summary['cases']), set(SCENARIOS))
            self.assertEqual(sum(c['scenario_gate_passed'] for c in summary['cases'].values()), 3)
            product, manifest = load_product(directory/'hill_junction.npz')
            self.assertGreater(manifest['report']['violations']['excessive_river_excavation'], 0)
            review = json.loads((directory/'review.json').read_text(encoding='utf-8'))
            self.assertEqual(len(review['locations']), 4)
            self.assertTrue(all(c['status'] == 'not_tested' for location in review['locations']
                                for c in location['checks']))
            with self.assertRaises(FileExistsError):
                run_scenarios(directory, 'unused', 'unused', self.compiler)

    def test_empty_scenes_cannot_qualify_as_water_acceptance(self):
        def empty(terrain_path, source_path, bounds, **kwargs):
            x0,x1,z0,z1 = bounds
            terrain = np.full((z1-z0,x1-x0), 100, dtype=np.int16)
            product, report = compile_scene(terrain, np.zeros(terrain.shape,dtype=bool), [],
                                            origin=(x0,z0))
            return product, report, Settings(), {}
        with tempfile.TemporaryDirectory() as temporary:
            summary = run_scenarios(Path(temporary)/'empty', 'unused', 'unused', empty)
            self.assertTrue(summary['complete'])
            self.assertFalse(summary['compile_gate_passed'])
            self.assertTrue(all(not c['scenario_gate_passed'] for c in summary['cases'].values()))


if __name__ == '__main__':
    unittest.main()

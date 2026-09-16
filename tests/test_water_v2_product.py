from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from water_v2.__main__ import main
from water_v2.blocks import iter_column_blocks
from water_v2.engine import compile_scene
from water_v2.model import DRY, FIELDS, Settings, load_product, save_product, validate


class ProductTests(unittest.TestCase):
    def test_report_cannot_hide_failures_stored_in_the_product(self):
        from water_v2.diagnostics import FLAGS
        product = self.flat_product([[True]])
        product['diagnostic_flags'][0,0] = FLAGS['unsupplied_water']
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'stored diagnostic flags'):
                save_product(Path(directory)/'false-pass.npz',product,
                             {'violations':{},'ready_for_world_write':True},Settings(),{})

    def test_river_pool_requires_an_explicit_standing_source_volume(self):
        product = self.flat_product([[True]])
        product['river_pool_mask'][0,0] = True
        with self.assertRaisesRegex(ValueError,'standing source volumes'):
            validate(product)

    def flat_product(self, wet, section=None):
        wet = np.asarray(wet, dtype=bool)
        product = {key: np.zeros(wet.shape, dtype=dtype) for key, dtype in FIELDS.items()}
        product["bounds"] = np.asarray([0, wet.shape[1], 0, wet.shape[0]], dtype=np.int32)
        product["water_mask"] = wet
        product["terrain_y"].fill(20)
        product["terrain_y"][wet] = 19
        product["surface_y"].fill(DRY)
        product["surface_y"][wet] = 20
        product["depth"][wet] = 1
        product["waterfall_top_y"].fill(DRY)
        product["receiver"].fill(-1)
        product["section_id"][wet] = np.arange(1, int(wet.sum())+1)
        if section is not None:
            product["section_id"] = np.asarray(section, dtype=np.int32)
        return product

    def lake(self):
        terrain = np.full((24,24),80,dtype=np.int16)
        body = np.zeros(terrain.shape,dtype=bool)
        body[3:21,3:21] = True
        return compile_scene(terrain,body,[])

    def test_serialization_roundtrip_retains_explicit_block_states_and_materials(self):
        product, report = self.lake()
        before = list(iter_column_blocks(product,12,12))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"candidate.npz"
            save_product(path,product,report,Settings(),{"test":"lake"})
            loaded, manifest = load_product(path)
            self.assertEqual(list(iter_column_blocks(loaded,12,12)),before)
            self.assertEqual(manifest["report"],report)
            with self.assertRaises(FileExistsError):
                save_product(path,product,report,Settings(),{})
            with path.open("ab") as handle:
                handle.write(b"tampered")
            with self.assertRaisesRegex(ValueError,"hash"):
                load_product(path)

    def test_cli_runs_with_only_raw_inputs_and_detects_infeasible_scene(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            terrain = np.full((24,32),80,dtype=np.int16)
            terrain[12,2:30] = 100
            np.save(root/"terrain.npy",terrain)
            np.savez(root/"sources.npz",waterbody_mask=np.zeros(terrain.shape,dtype=bool),
                     points_x=np.asarray([2,29]),points_z=np.asarray([12,12]),
                     offsets=np.asarray([0,2]),kind=np.asarray([3]),width_m=np.asarray([4.0]))
            with redirect_stdout(io.StringIO()):
                code = main(["compile","--terrain",str(root/"terrain.npy"),"--sources",str(root/"sources.npz"),
                             "--bounds","0","32","0","24","--output",str(root/"result.npz")])
            self.assertEqual(code,2)
            product,manifest = load_product(root/"result.npz")
            self.assertFalse(manifest["report"]["ready_for_world_write"])
            self.assertIn("excessive_river_excavation",manifest["report"]["violations"])
            self.assertFalse(manifest["world_written"])

    def test_manifest_gate_settings_and_provenance_cannot_change_independently(self):
        product = self.flat_product([[True]])
        from water_v2.diagnostics import FLAGS
        product['diagnostic_flags'][0,0] = FLAGS['unsupplied_water']
        report = {"ready_for_world_write": False, "violations": {"unsupplied_water": 1}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"diagnostic.npz"
            original = save_product(path, product, report, Settings(), {"source_sha256": "original"})
            for field, key, value in (("report", "ready_for_world_write", True),
                                      ("provenance", "source_sha256", "changed"),
                                      ("settings", "max_excavation", 100)):
                with self.subTest(field=field):
                    edited = json.loads(json.dumps(original))
                    edited[field][key] = value
                    path.with_suffix(".json").write_text(json.dumps(edited), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "manifest metadata"):
                        load_product(path)
            # Whitespace and JSON object key order do not alter the metadata.
            path.with_suffix(".json").write_text(json.dumps(original, sort_keys=True), encoding="utf-8")
            _, loaded_manifest = load_product(path)
            self.assertEqual(loaded_manifest, original)

    def test_every_planned_lake_column_can_be_emitted_without_reinterpretation(self):
        product,report = self.lake()
        self.assertTrue(report["ready_for_world_write"])
        for z,x in np.argwhere(product["water_mask"]|product["bank_mask"]):
            self.assertTrue(list(iter_column_blocks(product,int(x),int(z))))

    def test_schema_rejects_a_receiver_into_dry_terrain(self):
        product,_ = self.lake()
        product["receiver"][12,12] = 0
        with self.assertRaisesRegex(ValueError,"wet columns"):
            validate(product)

    def test_section_ids_must_describe_exactly_the_wet_cells(self):
        for section in ([[0, 0]], [[-1, 0]], [[1, 2]]):
            with self.subTest(section=section):
                product = self.flat_product([[True, False]], section)
                with self.assertRaisesRegex(ValueError, "section IDs"):
                    validate(product)

    def test_one_section_cannot_be_reused_across_a_dry_gap(self):
        product = self.flat_product([[True, False, True]], [[7, 0, 7]])
        with self.assertRaisesRegex(ValueError, "connected footprint"):
            validate(product)

    def test_one_section_cannot_have_separate_pieces_in_a_connected_water_body(self):
        product = self.flat_product([[True, True, True]], [[1, 2, 1]])
        with self.assertRaisesRegex(ValueError, "connected footprint"):
            validate(product)

    def test_diagonal_contact_does_not_connect_a_shared_section(self):
        product = self.flat_product([[True, False], [False, True]], [[1, 0], [0, 1]])
        with self.assertRaisesRegex(ValueError, "connected footprint"):
            validate(product)

    def test_section_surface_elevation_must_be_consistent(self):
        product = self.flat_product([[True, True]], [[1, 1]])
        product["surface_y"][0, 1] -= 1
        product["terrain_y"][0, 1] -= 1
        with self.assertRaisesRegex(ValueError, "one surface elevation"):
            validate(product)

    def test_receivers_cannot_jump_to_a_disconnected_water_component(self):
        for wet, target in (([[True, False, True]], 2),
                            ([[True, False], [False, True]], 3)):
            with self.subTest(wet=wet):
                product = self.flat_product(wet)
                product["receiver"][0, 0] = target
                with self.assertRaisesRegex(ValueError, "disconnected wet components"):
                    validate(product)

    def test_receiver_cycles_are_rejected_even_on_equal_stages(self):
        product = self.flat_product([[True, True, True]])
        product["receiver"][0] = [1, 2, 0]
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate(product)

    def test_receivers_can_span_a_connected_section_footprint(self):
        product = self.flat_product([[True]*5], [[1, 2, 2, 2, 2]])
        product["receiver"][0, 0] = 4
        self.assertEqual(validate(product), (1, 5))

    def test_long_acyclic_paths_and_sparse_section_ids_are_valid(self):
        length = 5000
        product = self.flat_product([[True]*length],
                                    [[np.iinfo(np.int32).max]*length])
        product["receiver"][0, :-1] = np.arange(1, length)
        self.assertEqual(validate(product), (1, length))

    def test_unresolved_terrain_cannot_pass_as_an_ordinary_dry_column(self):
        product = self.flat_product([[False, True]])
        product["terrain_y"][0, 0] = DRY
        with self.assertRaisesRegex(ValueError, "unresolved"):
            validate(product)

    def test_bounds_cannot_overflow_the_serialized_coordinate_or_receiver_dtype(self):
        for bounds in ([0, 2**31, 0, 1], [0, 65536, 0, 65536]):
            with self.subTest(bounds=bounds):
                product = self.flat_product([[True]])
                product["bounds"] = np.asarray(bounds, dtype=np.uint64)
                with self.assertRaisesRegex(ValueError, "fit int32"):
                    validate(product)

    def test_checked_array_like_boolean_fields_are_supported_consistently(self):
        product = self.flat_product([[True, False]])
        for name in ("water_mask", "standing_water_mask", "bank_mask"):
            product[name] = product[name].tolist()
        self.assertEqual(validate(product), (1, 2))

    def test_masked_values_cannot_hide_invalid_product_cells(self):
        product = self.flat_product([[True, False]])
        product["water_mask"] = np.ma.array(product["water_mask"], mask=[[True, False]])
        with self.assertRaisesRegex(ValueError, "masked values"):
            validate(product)

    def test_settings_reject_invalid_depth_and_world_range(self):
        for values in ({"max_lake_depth":256},{"max_river_depth":0},{"lake_shelf":float("nan")},
                       {"min_y":80,"max_y":40},{"river_incision":1.5}):
            with self.subTest(values=values),self.assertRaises(ValueError):
                Settings(**values)

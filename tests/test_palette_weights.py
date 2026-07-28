"""น้ำหนักใน PALETTE ต้องเป็นสัดส่วนที่ได้จริงบนแผนที่

บั๊กเดิม: r ที่ classify() สร้างเป็นระฆังคว่ำ (std 0.141) ส่วน pick() แบ่งช่วง
[0,1) ตามน้ำหนักสะสม ผลคือหยิบแต่ตัวกลางของรายการ วัดกับ rock_mid ได้
stone ตั้ง 0.26 -> 0.044 | dripstone ตั้ง 0.06 -> 0.000 (ไม่เคยโผล่เลย)
"""

import collections
import unittest

import numpy as np

import surface as S


def _raw_r(shape=(700, 700), seed=1236):
    grain = S.white_noise(0, 0, shape, seed)
    clump = S.smooth_noise(0, 0, shape, 20.0, seed + 1, 2)
    return np.clip(0.28 * grain + 0.72 * (clump * 0.5 + 0.5), 0.0, 0.999)


class UniformiseTests(unittest.TestCase):
    def test_raw_r_is_bell_shaped(self):
        """ยืนยันว่าปัญหามีอยู่จริง — ถ้าวันหนึ่งสูตร r เปลี่ยนจะได้รู้"""
        raw = _raw_r()
        self.assertLess(float((raw < 0.20).mean()), 0.10)

    def test_uniformised_r_is_flat(self):
        r = S.uniformise(_raw_r())
        for lo, hi in ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)):
            share = float(((r >= lo) & (r < hi)).mean())
            self.assertAlmostEqual(share, 0.2, delta=0.04, msg=f"ช่วง {lo}-{hi}")

    def test_uniformise_preserves_order(self):
        raw = np.linspace(0.0, 0.999, 200, dtype=np.float32)
        self.assertTrue((np.diff(S.uniformise(raw)) >= 0).all())

    def test_uniformise_stays_in_range(self):
        r = S.uniformise(_raw_r())
        self.assertGreaterEqual(float(r.min()), 0.0)
        self.assertLess(float(r.max()), 1.0)


class RealisedWeightTests(unittest.TestCase):
    def test_every_palette_realises_its_declared_weights(self):
        r = S.uniformise(_raw_r())
        for name, choices in S.PALETTE.items():
            out = S.pick(r, choices)
            counts = collections.Counter(out.ravel().tolist())
            for key, weight in choices:
                got = counts[S.IDX[key]] / out.size
                self.assertAlmostEqual(
                    got, weight, delta=0.03,
                    msg=f"{name}/{key}: ตั้ง {weight:.2f} ได้ {got:.3f}",
                )

    def test_no_palette_entry_is_dead(self):
        """ทุกบล็อกที่ประกาศไว้ต้องปรากฏจริง"""
        r = S.uniformise(_raw_r())
        for name, choices in S.PALETTE.items():
            out = S.pick(r, choices)
            present = set(out.ravel().tolist())
            for key, _weight in choices:
                self.assertIn(
                    S.IDX[key], present, f"{name}/{key} ไม่ปรากฏเลย"
                )

    def test_weights_sum_to_one(self):
        for name, choices in S.PALETTE.items():
            self.assertAlmostEqual(
                sum(w for _, w in choices), 1.0, places=6, msg=name
            )


class RetiredBlockTests(unittest.TestCase):
    def test_smooth_stone_is_not_used_on_terrain(self):
        """texture เรียบสนิทไม่มีลาย โดดออกจากหินธรรมชาติรอบข้างทุกตัว"""
        for name, choices in S.PALETTE.items():
            self.assertNotIn(
                "smooth", [k for k, _ in choices], f"{name} ยังใช้ smooth_stone"
            )
        self.assertNotIn("smooth", S.SLAB_OF)


if __name__ == "__main__":
    unittest.main()

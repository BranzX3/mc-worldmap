# M1 — ทิศทาง profile ลำน้ำ 2026-09-10

Candidate `direction_20260910_v2`; สถานะ **implemented / rework — ยังไม่รับรอง M1**
เทียบกับ [baseline_20260910](baseline_20260910.md) โดยคงเกณฑ์ morphology เดิม
งานรอบนี้แก้การเรียง profile ก่อน regularize; ไม่เปลี่ยนสูตรวัดหรือเพิ่มข้อยกเว้น

## ปัญหาและการแก้

`regularize_stage` ต้องรับ samples จากต้นน้ำไปปลายน้ำ แต่ข้อมูลต้นทางบางเส้น
เรียงจากต่ำไปสูง การบังคับระดับให้ไม่เพิ่มจึงกดทั้งช่วงลงและขุดร่องลึกเกิน DEM:

| พื้นที่ / source line | DEM ที่ปลายทั้งสอง | ผลเดิม |
|---|---|---|
| lake_mouth / 4646 | 77 → 108 | profile ราบใกล้ 77; incision ภายใน patch สูงสุด 25 บล็อก |
| hill_junction / 807 | 78 → 216 | incision ภายใน patch สูงสุด 134 บล็อก |

`line_profile_entries` เปรียบเทียบมัธยฐานของไม่เกิน 9 samples ที่ปลายทั้งสอง
ถ้าปลายท้ายสูงกว่าอย่างน้อย 2 บล็อก จะกลับลำดับพิกัดและ spatial constraints
พร้อมกันก่อน regularize และ lake pinning พื้นที่ราบหรือกำกวมยังคง source order
ใช้ทางเดียวกันทั้ง profile สำหรับ patch และ global; ไม่แก้ข้อมูล input บนดิสก์
การสะสม upstream length เพื่อคำนวณความกว้างยังอ้าง topology ของ source เดิม
จึงยังไม่ใช่การรับรองทิศทางเครือข่าย hydrology ทั้งแผนที่

Regression test ของ source ที่เรียงขึ้นเขาล้มบนโค้ด baseline ด้วย assertion
เรื่องทิศทาง และผ่านบนโค้ดใหม่ พร้อมกรณี endpoint outlier และพื้นที่ราบ
ดู [test](../../tests/test_profile_direction.py) และ
[failed-before log](../../logs/direction-before-regression.log)

## ผลตรวจ

รอบแรก `direction_20260910` ผ่าน 390 tests แต่ golden ล้มที่ prototype_stream
ด้วย `ArrayMemoryError` ใน `np.gradient` ของแผนที่ 10,000 × 10,000
เก็บ [log ที่ล้ม](../../archive/baselines/direction_20260910/golden.log) ไว้ตามเดิม
และไม่ใช้ snapshot นั้นรับรองผล golden

แก้ `hillside_rise_per_block` ให้คำนวณครั้งละ 256 แถว พร้อม halo สำหรับ gradient
และ smoothing แทนการสร้าง float/gradient arrays เต็มแผนที่พร้อมกัน เหลือเฉพาะ
ผลความชันเต็มแผนที่ ผลเทียบสูตรเดิมตรงกันทุกค่าใน tests ครอบคลุมขอบ map,
รอยต่อ batch, kernel คู่/คี่ และแผนที่เล็ก เก็บโค้ดแก้แล้วใน snapshot v2 ใหม่

**392 tests ผ่าน ไม่มี fail/error/skip; golden 11 patches สร้างใหม่ครบ;
hard invariants ทั้งสามชนิดเป็นศูนย์ทุก patch; snapshot/current SHA-256 และ
fingerprints ครบ 11 จุดผ่าน** รัน suite จบก่อนเริ่ม golden
ผล lake_mouth/hill_junction ตรงกับ probe ก่อนแบ่งคำนวณความชันทุก metric

| พื้นที่ | Canyon excess ก่อน → หลัง | Bank excess ก่อน → หลัง |
|---|---:|---:|
| lake_mouth | 13.26% → **12.43%** | -1.52% → -1.26% |
| hill_junction | 5.71% → **0.00%** | 4.95% → **5.42%** |

อีก 9 patches มี metrics ทุกค่าเท่ากับ baseline การตัดพื้นสูงสุดของ
hill_junction ลดจาก **135 เหลือ 7 บล็อก** และ bank wall cells ลดจาก 487 เป็น 224
แต่ยังไม่ผ่าน geometry gate เพราะ canyon ที่ lake_mouth และ bank ที่ hill_junction
เกินเกณฑ์ 5% จึงยังไม่ส่งชุดนี้ไป paint

Compare รายงาน regression 3 metrics ซึ่งต้องตรวจต่อพร้อมงาน morphology:

- hill_junction: terrain_lifted_cells 1,855 → 2,044
- hill_junction: wall_slope_ratio 2.530 → 4.243
- lake_mouth: tiny_pool_share 86.96% → 88.46%

การเพิ่ม bank excess ของ hill_junction ไม่ถูก compare flag เพราะเพิ่มน้อยกว่า
noise threshold 1 percentage point แต่ข้ามเกณฑ์ acceptance จึงยังถือว่าไม่ผ่าน
การหายไปของร่องลึกไม่ได้ชดเชย regressions เหล่านี้

## หลักฐาน

- [Metrics](direction_20260910.metrics.json), [run metadata](direction_20260910.run.json)
  และ [SHA-256 evidence manifest](direction_20260910.evidence.json)
- [Snapshot manifest](../../archive/baselines/direction_20260910_v2/snapshot.json)
  และ [suite log](../../archive/baselines/direction_20260910_v2/tests.log)
- [Golden log](../../archive/baselines/direction_20260910_v2/golden.log)
  และ [comparison](../../logs/direction-20260910-compare.log)
- [หน้าตัด hill_junction](../../golden/direction_20260910_v2/section_hill_junction.png)
  และ [หน้าตัด lake_mouth](../../golden/direction_20260910_v2/section_lake_mouth.png)
- npz และ fingerprints ที่ใช้วัดเก็บใน `golden/direction_20260910_v2/patches/`
  แยกจาก cache ที่รอบพัฒนาถัดไปสร้างทับได้; baseline เดิมยังคงไว้

## ขอบเขตที่ยังไม่ผ่าน

ผล probe ชี้ว่าการแก้ทิศทางไม่ได้ปิดปัญหา M1 ทั้งหมด:

- lake_mouth: หลังแก้ profile พบ 857 core water cells ที่ยังเป็น canyon อยู่ใน
  standing-water mask ทั้งหมด; จุดร้ายสุด `(2077, 3083)` มี DEM 124 แต่ผิวน้ำ 75
  ทำให้ตัดพื้น 49 บล็อก polygon เต็มมี DEM 46–124 จึงไม่ใช่แอ่งราบทั้งผืน
- `shape_standing_water_patch` เมื่อไม่ได้รับ `standing_surface` จัดทุก component
  เป็นน้ำนิ่งด้วย mode ภายใน patch แต่ global ใช้ `_component_standing_surface`
  แยกแอ่งราบ/น้ำบนพื้นที่ลาดและใช้ percentile นี่คือช่องว่างระหว่างสอง pipeline
  ที่ต้องแก้และทดสอบต่อ ไม่ใช่เหตุผลให้ยกเกณฑ์ canyon
- hill_junction: ร่องลึกหายแล้วใน probe แต่ bank excess เพิ่มข้ามเกณฑ์ 5%
  ต้องตรวจขั้นตลิ่งจากรูปทรงใหม่ก่อนรับรอง ไม่ใช้ noise threshold ของ compare
  กลบการข้าม acceptance threshold

**งานถัดไปหนึ่งเรื่อง:** ทำให้การจำแนก standing water และระดับน้ำของ patch/global
ใช้กฎเดียวกัน โดยรักษาผืนน้ำและการเชื่อมลำน้ำที่สมเหตุผล ต้องมี regression fixture
สำหรับ polygon ลาดชันที่ต่อแอ่งราบ, patch คร่อมขอบแอ่ง และระดับน้ำตรงกันเมื่อแบ่ง tile
จากนั้นรัน 11 patches เทียบ candidate นี้; ตรวจ bank regression ที่ hill_junction
ก่อน build/paint/readback ไม่ลดพื้นที่น้ำเพียงเพื่อทำคะแนนให้ผ่าน

ยังไม่มี world readback หรือภาพ/การเดินในเกมของ candidate นี้

# Paint readiness — 2026-09-13

Candidate `paint_ready_20260913_v3` ผ่าน geometry gate และ build/paint/readback
ครบ 4 จุดแล้ว พร้อมเปิดเซฟทดลองในเกม
ยังไม่ใช่ visual acceptance
ใช้ code/input snapshot ใน `archive/baselines/paint_ready_20260913_v3/`
และเปรียบเทียบกับ [standing_20260912](standing_20260912.md)

## ปัญหาและการแก้

- ตลิ่งที่ถูกขุดติดกับขอบน้ำซึ่งยกเพียง 1 บล็อกเคยเหลือขั้นเกินจำเป็น:
  คืนส่วนที่ถูกขุดถึง DEM และแก้ notch จาก seal ภายในเพดาน DEM+1
- ผืนน้ำไหลกว้างที่อยู่นอก centerline เคยลึก 1 บล็อกเท่ากันและไม่มี flow:
  คำนวณความลึกจากระยะฝั่ง และทิศ/แรงไหลจากระดับน้ำบนทางเชื่อมที่เปียกจริง
- การยืดแอ่งและปัดพิกัดทำให้ slope เล็กกลายเป็นน้ำตก:
  ตรวจขอบ DEM หลัง cardinalization และรักษาเพดานนั้นระหว่าง lake pinning
  หน้าผาจริงยังคงน้ำตกได้ ไม่มีการเพิ่ม feature mask เพื่อยกเว้นจุดที่ผิด
- เพิ่ม `paint_trial.py`: ตรวจ snapshot, suite, metrics, product hash และ preflight
  ก่อน backup save แบบตรวจ hash แล้วรัน build → paint → reopen/readback
  ทุก child ใช้ frozen source, product และ save เดียวกัน

## ขอบเขตการรับรอง

Golden มี 11 จุด การทดสอบเซฟมี 4 จุด จุดละ 256 × 256 บล็อก
วิธีรันและพิกัดอยู่ใน [PAINT_TEST](../PAINT_TEST.md)
เซฟต้นฉบับ `mmotest` แยกจากเซฟทดลอง `mmotest-paint-20260913`
ยังต้องเปิดเกม รอ fluid tick และเดินตรวจ ก่อนรับรอง M1 ด้านภาพหรือ M2 route

## ผลตรวจ geometry

Full suite จาก frozen workspace: **409 tests ผ่าน ไม่มี failure/error/skip**
ตรวจ SHA-256 snapshot/current และ shape fingerprints ครบทั้ง 11 patches
Hard invariants (`dry_bank_below_water`, `uncovered_drop`, `unflat_cross_runs`)
เป็นศูนย์ทุกจุด; bank/canyon excess ไม่เกิน 5%; water mask เท่ากับ standing
ทุก cell ทั้ง 11 จุด ไม่ได้ลดจำนวนน้ำเพื่อให้ตัวเลขผ่าน

| จุด | Canyon excess | Bank excess ก่อน → หลัง | ขั้นตลิ่งสูงสุด |
|---|---:|---:|---:|
| lake_mouth | 0.044% | -0.46% → -1.26% | 7 → 2 |
| hill_junction | 0.00% | 5.42% → -1.35% | 5 → 5 |
| steep_stream | -0.13% | 3.09% → -3.56% | 9 → 9 |
| player_liked | -2.43% | -0.83% → -4.22% | 10 → 10 |

Excess คือส่วนต่างจาก DEM; ค่าติดลบหมายถึงเกิดน้อยกว่าภูมิประเทศเดิม
ขั้นเกิน 4 บล็อกที่เหลือใน core มี 26 bank cells และทุกขอบเชื่อมกับกลุ่ม
lip/curtain/pool ที่มี lip รองรับด้วย DEM จริง การตรวจพิจารณาปลายทั้งสอง
ของขอบตลิ่งที่วัด ไม่ขยายรัศมี feature เพื่อให้ผ่าน
ดู [feature audit](paint_ready_20260913_v3.features.json)

## Compare flags ที่ต้องตรวจในเกม

เทียบ standing พบ **11 soft regression flags** ไม่ได้ยกเลิกหรือเปลี่ยนเกณฑ์:

- hill_junction และ steep_stream: bare-wall share เพิ่ม 1.19 และ 1.41 จุดเปอร์เซ็นต์
  shore share ลดเท่ากัน (เป็น metric คู่ตรงข้าม รวม 4 flags) แม้จำนวน bank-wall cells ลด
- lake_mouth: bank-wall cells 4 → 14, wall-slope ratio 2.53 → 2.83 (2 flags)
  ขณะ bank climb สูงสุดลด 7 → 2; ต้องดูรูปร่างริมฝั่งในเซฟจริง
- player_liked: bed-flat share 30.73% → 32.85%, tiny-pool share 66.18% → 67.62%
  (2 flags) ตรวจว่าฉากที่เคยชอบยังดูเป็นธรรมชาติ
- prototype_stream: bed-flat share 29.71% → 32.68%, tiny-pool share 61.96% → 63.77%,
  canyon p90 5 → 6 บล็อก (3 flags) หลังบังคับ ordinary profile steps
  Canyon excess ยังติดลบ; หน้าตัดใช้ดูประกอบได้แต่ไม่แทนการเดินเกม

ความลึกน้ำสูงสุด 5 บล็อกเท่ากันทั้ง 11 จุดเป็นคำเตือนเชิงสถิติของ harness
ตรวจจาก NPZ จริงและสัมพันธ์กับเพดาน bed/pool ที่ใช้ ไม่ได้กรอกค่าคงที่ใน metrics
ภาพ section_lake_mouth และ section_steep_stream ถูกเปิดเทียบกับ standing แล้ว
ขอบเขตการยอมรับรอบนี้คือ **พร้อมทดลอง paint แบบจำกัดพื้นที่**;
flags เหล่านี้ยังรอ world visual review ก่อนปิด M1

ผลตัวเลขครบอยู่ใน [metrics](paint_ready_20260913_v3.metrics.json)
และ [หลักฐานพร้อม SHA-256](paint_ready_20260913_v3.evidence.json)

## ผลเขียนเซฟและอ่านกลับ

Run `archive/paint_trials/20260913T075215499565Z/` ผ่านทั้ง **12 ขั้น**
ใช้ frozen source กับ published NPZ ที่ตรวจ hash แล้ว และ backup save ก่อนเขียน
writer ปัดกรอบ 256 ไปตาม chunk; readback ตรวจกรอบที่ร้องขอ 256 × 256 ทุกจุด

| จุด | คอลัมน์น้ำที่อ่านกลับ | Hard errors | ต้นไม้ / พืชจาก paint |
|---|---:|---:|---:|
| lake_mouth | 5,042 | 0 | 168 / 14,390 |
| hill_junction | 2,296 | 0 | 9 / 5,295 |
| steep_stream | 1,928 | 0 | 15 / 2,300 |
| player_liked | 1,360 | 0 | 106 / 8,626 |
| รวม | **10,626** | **0** | 298 / 30,611 |

ทุกจุด missing/wrong/bed-fluid/above-top/non-source errors = 0
paint save validation ผ่านจุดละ 16 samples; จำนวนต้นไม้/พืชเป็นสถิติ writer
ไม่ใช่การตรวจต้นไม้ทุกต้นหลังเปิดเกม มีการ schedule fluid ticks แต่ยังไม่ได้
รันเกมเพื่อรับรองผลหลัง tick

วัสดุจาก readback: ลำน้ำมี cobblestone 66.0–80.3% ตามจุด; ทะเลสาบที่ lake_mouth
เป็น gravel 60.3% และ cobblestone 36.0% ต้องตรวจว่าภาพจริงกลมกลืนเพียงใด
ไม่ถือว่าการเขียนบล็อกถูกต้องพิสูจน์คุณภาพ immersion แล้ว

หลักฐานเซฟสุดท้ายและ hash logs อยู่ใน
[world evidence](paint_ready_20260913_v3.world.json)
เซฟ `mmotest` ต้นฉบับตรวจ SHA-256 ครบ 832 ไฟล์เท่าเดิม
งานถัดไปหนึ่งเรื่องคือเปิด **mc-worldmap Paint Test 2026-09-13**
และตรวจสี่จุดตาม [PAINT_TEST](../PAINT_TEST.md) ก่อนปิด M1 ด้านภาพ

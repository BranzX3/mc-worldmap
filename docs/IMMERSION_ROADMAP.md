# คิวพัฒนาสู่ Immersion Slice v1

เป้าหมายและขอบเขตอ้าง [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)
สถานะรับรองอ้าง [IMMERSION_REVIEW.md](IMMERSION_REVIEW.md)
ปรับคิว 2026-09-14 ตามคำขอเขียนระบบน้ำใหม่แบบ greenfield
ทำ implementation หลักครั้งละหนึ่งเรื่อง พัฒนาและรัน local เท่านั้น
ทำ dev ก่อน รัน build/test ที่เกี่ยวข้องตอนท้ายรอบ

## คิวปัจจุบัน

| ลำดับ / milestone | ผลส่งมอบ | Dependency | หลักฐานปิดงาน |
|---|---|---|---|
| 1 / W1 | compiler น้ำใหม่: raw DEM/OSM → topology/router → shared level solver → explicit product → literal block sink | runtime local ที่มีแล้ว | สัญญา topology/ระดับ/วัสดุ/พืช/fluid และ provenance ตรวจได้; ไม่มี logic น้ำเดิมตัดสินซ้ำ; tests และ diagnostics ผูกกับ code/input ชุดเดียวกัน |
| 2 / W2 | scene ข้อมูลจริงที่ไม่มีข้อขัดแย้งทางกายภาพ | W1 | `lake_mouth`, `hill_junction`, `steep_stream`, `player_liked` ผ่าน compile gates พร้อมตรวจ topology/ระดับ/การขุด/แหล่งจ่ายน้ำ; ไม่ปิด error ด้วยการยกฝั่งหรือผ่อนเกณฑ์เฉพาะจุด |
| 3 / W3 → M1 | writer อิสระของระบบใหม่และเซฟทดลองที่ตรวจซ้ำได้ | W2 ในพื้นที่ที่จะเขียน | backup + preflight, เขียนตาม product, reopen/readback ตรงทั้งบล็อกและ fluid state, ตรวจน้ำหลัง tick และภาพระดับสายตาไม่มี blocker/major |
| 4 / M2 | ตรึงเส้นทางสำรวจต่อเนื่อง 10–15 นาที | สำรวจข้อมูลได้ก่อน; รับรองโลกหลัง W3 | waypoints, forest–meadow/water, จุดค้นพบ 3 บทบาท และเวลาเดินจริง |
| 5 / M2 | ฉากรวมบน route และทางข้าม/ทางลง/ที่กำบังเท่าที่จำเป็น | 4 | เดินครบโดยไม่บิน/teleport/แก้บล็อกช่วย; object คร่อม region และรอยต่อไม่มี major defect |
| 6 / M2 | เจ้าของโปรเจกต์รีวิว Immersion Slice v1 | 3–5 | ภาพคงที่อย่างน้อย 6 จุด, technical gates ผ่าน, รับรอง 4 เสาหลักตาม PROJECT_DIRECTION |
| 7 / M3 | กฎเดียวกันในพื้นที่อิสระอย่างน้อย 2 แห่งและ global candidate | 6 | ไม่มีการแก้เฉพาะพิกัดเพื่อผ่าน; global topology/levels, seams, readback และ walkthrough ตัวแทนผ่านจาก candidate เดียวกัน |

**งานหลักตอนนี้คือ W1/W2:** compiler ทำงานบน scene ที่มีขอบเขตจำกัดแล้ว
แต่ข้อมูลจริงยังมี physical violations ต้องแก้ก่อนเขียนเซฟ ดูพิกัดและผลตรวจ
ใน [รายงานระบบน้ำใหม่](reviews/water_v2_20260914.md) และสัญญาใน
[WATER_V2.md](WATER_V2.md) สถานะ `ready_for_world_write` ของ compile report
เป็นผลตรวจ product เท่านั้น ไม่ใช่การรับรอง writer, น้ำหลัง tick หรือภาพในเกม

W3 ต้องมี writer ที่ไม่เรียก logic น้ำเดิมมาตัดสินความลึก วัสดุหรือพืชใหม่
หลัง compile เซฟ **mc-worldmap Paint Test 2026-09-13** เป็นระบบเก่า
และไม่มีน้ำจาก rewrite นี้ การเปิดเซฟเดิมไม่ปิด W3

พักงานเพิ่ม noise/decoration, arête/glacier นอก route และการตกแต่งทั้งแผนที่
ไว้ก่อน Forest/meadow และ shelters ที่มีโค้ดแล้วให้ตรวจเมื่อ route ต้องใช้
ไม่เพิ่มชนิดพืชเพื่อเลี่ยงปัญหาน้ำที่ยังไม่ผ่าน

## หลักฐานย้อนหลังของระบบเดิม

ผลต่อไปนี้อธิบายงานที่ผ่านมา ไม่ใช่คิวให้กลับไปแก้ shaper เดิมหรือหลักฐาน
รับรอง `water_v2` จำนวน tests ของรอบใหม่ให้ดูรายงานของรอบนั้น

| รอบ | สิ่งที่พิสูจน์ในรอบนั้น | หลักฐาน |
|---|---|---|
| Environment 2026-09-10 | Python 3.11.9, dependency และ full preflight ใช้งานได้; 384 tests ผ่าน | [IMMERSION_REVIEW](IMMERSION_REVIEW.md), [ENVIRONMENT](ENVIRONMENT.md) |
| `baseline_20260910` | snapshot SHA-256, 387 tests และ golden 11 patches | [รายงาน baseline](reviews/baseline_20260910.md) |
| `direction_20260910_v2` | แก้ทิศ source profile และ memory ของ slope; 392 tests; ยังมี bank/canyon regressions | [รายงาน direction](reviews/direction_20260910.md) |
| `standing_20260912` | รวม classification/ระดับน้ำนิ่ง; 395 tests; ยังมี compare regressions | [รายงาน standing](reviews/standing_20260912.md) |
| `paint_ready_20260913_v3` | 409 tests, golden 11 patches hard errors = 0, bank/canyon ผ่าน; readback 4 จุด รวม 10,626 คอลัมน์น้ำ hard errors = 0; visual/post-tick ยังไม่รับรอง | [รายงาน candidate](reviews/paint_ready_20260913_v3.md), [เซฟเก่าและวิธีรัน](PAINT_TEST.md) |

ผล `bank_final`, `hydrology_global3` และตัวเลขเก่ากว่านี้อยู่ใน
[TODO](TODO.md), [DECISIONS](DECISIONS.md) และรายงานที่เกี่ยวข้อง
ห้ามใช้ชื่อ `final` หรือผล unit test แทนการรับรองประสบการณ์ในโลกจริง

# สถานะการเขียนเซฟและวิธีทดสอบ candidate เก่า

**อัปเดต 2026-09-14:** งานปัจจุบันคือระบบน้ำ greenfield `water_v2/`
ยังไม่มีการเขียนระบบใหม่ลงเซฟ และยังไม่พร้อมทดสอบ rewrite ในเกม
ใช้ [WATER_V2](WATER_V2.md) สำหรับ compile/inspect และ
[รายงานระบบใหม่](reviews/water_v2_20260914.md) สำหรับผล physical gates
ที่ยังไม่ผ่าน ก่อนเขียนจริงต้องผ่าน W2 และมี writer/readback ของ W3

ส่วนที่เหลือของเอกสารนี้เป็น **ขั้นตอนและหลักฐานของระบบเก่า**
`paint_ready_20260913_v3` ตาม [รายงาน candidate](reviews/paint_ready_20260913_v3.md)
อย่านำ product `water_v2` ไปใส่ `paint_trial.py` หรือ painter เดิม: ยังไม่มี
integration ที่รับรองสัญญา geometry/material/fluid/plant ของระบบใหม่

## Save ทดลองของระบบเก่า

สำเนาอยู่ในโปรไฟล์ Fabulously Optimized:

```text
C:\Users\User\AppData\Roaming\ModrinthApp\profiles\Fabulously Optimized\saves\mmotest-paint-20260913
```

ชื่อที่แสดงในเกม: **mc-worldmap Paint Test 2026-09-13**
Build → paint → readback ระบบเก่าครบ 4 จุดแล้ว เซฟนี้ยังใช้ดูผลเก่าได้
การเปิดเซฟนี้ไม่ใช่การทดสอบ rewrite ผล readback รวม 10,626 คอลัมน์น้ำ
hard errors = 0; หลักฐานรันอยู่ใน `archive/paint_trials/20260913T075215499565Z/`
คัดลอก 832 ไฟล์จาก `mmotest` และตรวจ SHA-256 ก่อนเริ่มทดสอบ
Amulet เปิดสำเนาได้และอ่าน bounds ของ overworld เป็น y=-64..719
บันทึกการคัดลอกอยู่ใน `archive/paint_trials/clone-20260913/source-copy.json`

## คำสั่งย้อนหลัง: ตรวจความพร้อมก่อนเขียนระบบเก่า

ปิด save ทดลองในเกมและตัวแก้โลกก่อนรัน จาก root โปรเจกต์:

```powershell
.\run.ps1 paint_trial.py --candidate paint_ready_20260913_v3 --world 'C:\Users\User\AppData\Roaming\ModrinthApp\profiles\Fabulously Optimized\saves\mmotest-paint-20260913'
```

คำสั่งนี้อ่านอย่างเดียว ตรวจ snapshot กับ workspace, log ของ full suite,
hash ของ npz, วัด metrics จาก product ใหม่ครบ 11 จุด และตรวจ preflight ของ save
ที่ระบุจริง พร้อมพิมพ์กรอบเขียนและจำนวนคอลัมน์น้ำที่จะตรวจกลับ
ถ้า source/input เปลี่ยนจะไม่ถือว่าผ่าน candidate เดิม ซึ่งเป็นสิ่งที่คาดได้
ระหว่าง rewrite ไม่ข้าม fingerprint gate และไม่แก้ product ใหม่ให้เข้าระบบเก่า

## คำสั่งย้อนหลัง: Build → paint → reopen/readback

เมื่อคำสั่งตรวจพร้อมและตั้งใจทดสอบเขียน ให้เพิ่ม `--run`:

```powershell
.\run.ps1 paint_trial.py --candidate paint_ready_20260913_v3 --world 'C:\Users\User\AppData\Roaming\ModrinthApp\profiles\Fabulously Optimized\saves\mmotest-paint-20260913' --run
```

ค่าเริ่มต้นขอกรอบ 256 × 256 บล็อกต่อจุด ด้วย hydrology context 384 × 384
writer ปัดกรอบออกตามขอบ chunk 16 บล็อก (256–289 chunks ต่อจุดรอบนี้)
readback ตรวจคอลัมน์น้ำทั้งหมดในกรอบ 256 × 256 ที่ระบุ:

| ลำดับ | จุด | Center x,z | สิ่งที่ต้องตรวจในเกม |
|---|---|---|---|
| 1 | lake_mouth | 2242,3057 | ปากน้ำ, ทางลงน้ำ, ก้นน้ำกว้าง และพืชน้ำหลัง tick |
| 2 | hill_junction | 3344,480 | เดินเลียบตลิ่งและจุดบรรจบโดยไม่ติดขั้นที่สร้างขึ้น |
| 3 | steep_stream | 5792,5384 | กลุ่ม lip/curtain/pool และวัสดุของลำน้ำเชี่ยว |
| 4 | player_liked | 6165,6068 | ฉากที่เคยชอบยังอ่านเป็นภูมิประเทศธรรมชาติ |

คำสั่ง teleport จากผล build (ใช้ creative/spectator สำหรับย้ายจุด):

```text
/tp @s 2248 105 3064
/tp @s 3344 239 480
/tp @s 5792 147 5384
/tp @s 6168 121 6072
```

เดินเลียบฝั่ง มองน้ำตกจากทั้งด้านบนและด้านล่าง แล้วกลับมาดูหลังน้ำ tick
บันทึกภาพพร้อมพิกัดและอาการ เช่น น้ำแผ่ข้ามฝั่ง, ผนังสูงขวางทาง หรือก้นน้ำดูซ้ำ
ตรวจ soft compare flags ตาม [รายงาน candidate](reviews/paint_ready_20260913_v3.md)

เลือกจุดเดียวได้ด้วย `--patches lake_mouth` ตัวรันใช้ source ใน snapshot
และ product ชุดเดียวกันทั้งสามขั้น โดยกำหนด save ให้ทุก child process ชัดเจน
ตรวจ session lock ซ้ำก่อนแต่ละขั้น และหยุดทันทีเมื่อขั้นใดล้มเหลว

แต่ละ run เก็บใน `archive/paint_trials/<run-id>/`:

- `plan.json`: source snapshot, save, patch bounds และ product hashes
- `save-before/` + `backup-manifest.json`: สำเนา save ก่อนเขียนที่ตรวจ hash แล้ว
- `<patch>-build.log`, `<patch>-paint.log`, `<patch>-readback.log`
- `result.json`: exit code และ hash ของ log แต่ละขั้น; `complete=true` เมื่อครบทั้งหมด

Readback ของระบบเก่าเปิด region หลัง writer ปิดแล้วและตรวจทุกคอลัมน์น้ำ
ในกรอบเขียน จะผ่านเมื่อ missing/extra/wrong/source/bed errors เป็นศูนย์
เกณฑ์ all-source เดิมไม่ใช้กับ `water_v2` ซึ่งระบุ source/flow/falling โดยตรง

## การรีวิวและย้อนผลของรอบเก่า

เปิด save ทดลองในเกม รอให้น้ำ tick แล้วตรวจสี่จุดตามตาราง บันทึกภาพระดับสายตา
และ defect ตาม [IMMERSION_REVIEW](IMMERSION_REVIEW.md) การ teleport ระหว่าง
regression patches ไม่ใช่การรับรองเส้นทาง immersion ต่อเนื่องใน M2

หากต้องย้อนผล ให้ปิด save ทดลอง ย้ายโฟลเดอร์ผลทดสอบปัจจุบันไปเก็บชื่อใหม่
แล้วคัดลอก `save-before/` ของ run ที่เลือกกลับมาเป็น `mmotest-paint-20260913`
ตรวจไฟล์กับ `backup-manifest.json` ก่อนเปิดเกม เก็บสำเนาทั้งสองไว้จนตรวจสำเร็จ

# สถานะและหลักฐาน Immersion

เอกสารนี้เป็นสถานะ acceptance กลาง อัปเดตเมื่อมีหลักฐานใหม่เท่านั้น
เป้าหมายและ milestone อยู่ใน [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)
ตัวเลขย้อนหลังใน TODO/spec/checklist ไม่ใช้เปลี่ยนสถานะในตารางนี้โดยอัตโนมัติ

## สถานะ ณ 2026-09-14

| รายการ | สถานะ | หลักฐาน / สิ่งที่ต้องเติม |
|---|---|---|
| M0 environment | มี runtime local ที่ใช้งานได้ | `.venv311` Python 3.11.9 และ `run.ps1`; ผล environment วันที่ 10 อยู่ด้านล่าง ตรวจ preflight ของเซฟจริงใหม่ก่อนเขียนทุกครั้ง |
| W1 compiler น้ำใหม่ | implemented, อยู่ระหว่างตรวจและแก้ | `water_v2/` แยกจาก logic น้ำเดิม; ผล tests, provenance และข้อจำกัดปัจจุบันอ้าง [รายงาน](reviews/water_v2_20260914.md) และ [evidence JSON](reviews/water_v2_20260914.evidence.json) |
| W2 ข้อมูลจริง | ยังไม่ผ่าน physical gates | compiler ทำงานใน `bounded_scene`; ยังมี topology/ระดับ/แหล่งจ่ายน้ำที่ต้องแก้ตาม diagnostics ในรายงาน ไม่รับรอง global จาก scene crop |
| W3 / M1 น้ำในโลกจริง | ยังไม่เริ่มรับรองระบบใหม่ | ยังไม่มีการเขียน `water_v2` ลงเซฟ; ต้องมี writer อิสระ, reopen/readback และการตรวจหลัง tick/ภาพในเกม |
| M2 route และ immersion | ยังไม่ทดสอบ | มี candidate cluster ใน PROJECT_DIRECTION; ยังไม่ตรึง route/เวลาเดิน |
| Forest/meadow coherence | implemented, awaiting evidence | โค้ด/บันทึกใน TODO; ต้องดู object และฉากร่วมใน save |
| Shelters/arête/glacier detail | ทดลอง; ยังไม่รับรอง | feature opt-in ตาม spec; ไม่ถือว่าผ่านจาก unit test |
| M3 global candidate | ยังไม่เริ่มรับรอง | global3 เป็น artifact เก่า ใช้ยืนยันโค้ดปัจจุบันไม่ได้ |

`ready_for_world_write` ใน compile report หมายถึงผ่านการตรวจ product ที่มี
ใน compiler เท่านั้น ไม่ได้หมายความว่า world writer หรือ vanilla fluid ticks
ผ่านการทดสอบแล้ว สถานะ W3 ต้องมีหลักฐานของตนเอง

## หลักฐานระบบน้ำเดิม — ไม่ใช่ acceptance ของ rewrite

- [baseline_20260910](reviews/baseline_20260910.md): snapshot SHA-256 ผ่าน,
  387 tests และ golden 11 patches เป็น baseline ของระบบเดิม
- [paint_ready_20260913_v3](reviews/paint_ready_20260913_v3.md): 409 tests,
  11 patches hard invariants = 0, bank/canyon ผ่านและ water mask คงเดิม;
  build/paint/readback 4 จุด รวม 10,626 คอลัมน์ hard errors = 0 มี 11 soft
  compare flags และยังไม่มีการรับรอง visual/post-tick
- เซฟ **mc-worldmap Paint Test 2026-09-13** มี candidate เก่านี้เท่านั้น
  ดูพิกัดและขั้นตอนย้อนหลังใน [PAINT_TEST](PAINT_TEST.md)

คำขอ greenfield เปลี่ยนคิวงานจาก review candidate เก่าเป็น W1 → W2 → W3
ผลเก่ายังเก็บอ้างอิง แต่ไม่ปิด gate ของระบบใหม่โดยอัตโนมัติ

## ผลตรวจท้ายรอบจัดทิศทาง — 2026-09-10

**ข้อมูลย้อนหลัง:** ผลชุดนี้ถูกแทนที่ด้าน environment ด้วยผล 384 tests ด้านล่าง
ผลเดิมเกิดจากใช้ Python 3.13 และข้อจำกัด sandbox ไม่ใช่หลักฐานว่า `.venv311` เสีย

รอบนี้แก้เอกสารและแผน ไม่ได้เปลี่ยน generator หรือ build/paint โลกใหม่
ตรวจโค้ดและ working tree ที่มีอยู่เพื่อประเมินความพร้อมของ M0:

| การตรวจ | ผล |
|---|---|
| `.venv311\Scripts\python.exe -m unittest discover -s tests` | เริ่มไม่ได้: launcher สร้าง process จาก Python311 ที่อ้างไว้ไม่ได้ |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | รัน 195 tests: failures=1, errors=15, skipped=4; ยังไม่ผ่าน suite และบางโมดูล import ไม่สำเร็จ |
| สาเหตุที่ปรากฏในผล suite | ขาด `scipy`/`amulet_nbt`; import check ล้มตาม dependency; `test_datapack_matches_config` หา client jar ไม่พบ |
| Local Markdown links | ผ่านทั้ง 9 เอกสารที่เพิ่ม/แก้ในรอบนี้ |
| Git diff whitespace check | ผ่านเมื่อรองรับ CRLF ของ Windows (`core.whitespace=cr-at-eol`) |
| World preflight/readback/walkthrough | ไม่ได้รันในรอบจัดทิศทาง; รายงาน ACL/session lock เดิมยังเป็นข้อมูลย้อนหลัง |

จำนวน tests นี้ไม่ใช่ coverage ของ suite ที่ติดตั้ง dependency ครบแล้ว และ
ไม่ใช่ผลรับรองฟีเจอร์ใด ต้องแก้ runtime/dependency/client jar ที่เกี่ยวข้องแล้ว
รันใหม่ท้ายรอบ M0 ก่อนทำเครื่องหมายว่าผ่าน

## ผลรอบ environment — 2026-09-10 (หลักฐานย้อนหลัง)

ใช้ `.venv311` เดิมได้จริงเมื่อรันนอก sandbox และติดตั้ง GIS ที่ขาดเพิ่มเติม
ตรึง dependency หลักใน `requirements-runtime.txt`/`requirements.txt`
เพิ่ม `run.ps1` เพื่อเลือก Python 3.11 และ UTF-8 ให้ตรงกันทุกครั้ง
ปรับ preflight ให้ลอง import จริง ตรวจ schema jar และ handle ของ save โดยไม่แก้ bytes
รองรับ `dimensions/minecraft/overworld/region`; การค้น jar แยกสิทธิ์ถูกปฏิเสธ
ออกจากไฟล์ที่ไม่มี และไม่ fallback เมื่อ CLIENT_JAR ที่ระบุผิด

| การตรวจ | ผล / หลักฐาน local |
|---|---|
| Interpreter | Python 3.11.9 จาก `.venv311/Scripts/python.exe` |
| `pip check` | No broken requirements found |
| `run.ps1 preflight_environment.py --full` | `ready=true`, imports ทั้ง runtime/GIS ผ่าน, `missing_packages=[]`, `world_session_locked=false` |
| Game schema | `%APPDATA%/.minecraft/versions/26.2/26.2.jar`, version 26.2 / data_major 107 |
| Save access | เปิด handle ของ level.dat และ region ตัวอย่างได้; ไม่ใช่การตรวจ ACL ครบทุกไฟล์ |
| Full suite | **384 tests, OK, ไม่มี failures/errors/skips**; `logs/environment-tests-2026-09-10.log` |
| Preflight JSON | `logs/environment-preflight-2026-09-10.json` |

ในรอบนั้นมีข้อจำกัด sandbox และใช้การอนุญาตคำสั่งนอก sandbox ตามระบบ
ไม่ได้แก้ ACL, ลบ lock หรือ build/paint โลกใหม่ ผลนี้ปิดงาน environment ใน M0
ของรอบนั้น แต่ไม่รับรอง morphology หรือ immersion จาก suite อย่างเดียว
ดูวิธีใช้งานใน [ENVIRONMENT.md](ENVIRONMENT.md)

## แบบบันทึกต่อหนึ่ง candidate

คัดลอกแบบนี้ไป `docs/reviews/<candidate-id>.md` เมื่อเริ่มรอบรับรอง แล้วลิงก์จาก
ตารางสถานะ เก็บรายงานข้อความ/metadata ใน git; ภาพและ world snapshot เก็บ local
พร้อมพาธและ hash ที่ตรวจย้อนกลับได้ (ไฟล์ภาพ/golden หลายชุดถูก .gitignore)
การตั้งชื่อ `final` หรือ `latest` ไม่ใช่หลักฐานผ่าน

```text
Candidate ID / วันที่:
Milestone และปัญหาที่กำลังปิด:
สถานะ: planned | implementing | awaiting evidence | accepted | blocked
Code revision + dirty diff snapshot/hash (ถ้ามี):
Python executable/version + dependency versions:
Input/config fingerprint + flags:
Engine/schema + compile bounds/domain + physical report:
Water product + manifest/hash (ชุดเดียวกับที่เขียน):
Golden tag + run.json + metrics.json (ถ้าใช้ pipeline เดิม):
World save/snapshot + เวลา build/paint/reopen:
Client version + resource/shader packs + FOV/render distance/weather/time:
ผู้ตรวจ:

Route: จุดเริ่ม/จุดจบ/waypoints (x,y,z), โหมดเดินและเวลา:
จุดค้นพบ 3 บทบาท + พิกัด:
จุดภาพคงที่ >=6: พิกัด/yaw/pitch + พาธภาพ:
ช่วงคร่อม region / forest–meadow / ช่วงน้ำที่ตรวจ:

Technical: preflight, suite (pass/fail/skip), compile gates, world block/fluid readback:
Post-tick: เวลาที่รอ/พื้นที่ที่โหลด + fluid state และภาพก่อน/หลัง:
Global verifier/seam report (เฉพาะ candidate ระดับโลก):
Walkthrough: อ่านพื้นที่ / เดินครบ / อยากสำรวจต่อ / รายละเอียดต่อเนื่อง:
Defects: ID, severity, พิกัด, อาการ, หลักฐาน, วิธีทำซ้ำ, ผู้รับผิดชอบ:
ข้อยกเว้นธรรมชาติที่กำหนดก่อนประเมิน + เหตุผล/พิกัด/ผู้รับรอง:
ผล: accepted / rework / blocked พร้อมเหตุผล:
งานถัดไปหนึ่งเรื่อง:
```

## วิธีตัดสินโดยไม่ไล่คะแนนลวง

- **Blocker:** save เปิดไม่ได้, น้ำ/วัตถุผิดจนพื้นที่ใช้งานไม่ได้, route ขาดหรือ
  ต้องบิน/teleport/แก้บล็อกเพื่อไปต่อ ผลรับรองต้องไม่มีเลย
- **Major:** ผนังน้ำ/คันดินสังเคราะห์, ต้นไม้ขาด, transition แข็ง, พื้นที่ชวนเข้าแต่
  เข้าไม่ได้ หรือไม่อ่านทางไปต่อได้จนทำลายประสบการณ์หลัก ต้องแก้ก่อนรับรอง
- **Minor:** รายละเอียดเฉพาะจุดที่ไม่ทำลายเสาหลัก บันทึก backlog ได้พร้อมเหตุผล
  ผู้รับรองยอมรับ ไม่ปล่อยเงียบและไม่ใช้คะแนนเฉลี่ยกลบ blocker/major

เดินครั้งแรกด้วยการเคลื่อนที่เทียบผู้เล่นปกติ ตาม route ที่ตรึงไว้และ settings
เดียวกัน บันทึกเวลาหยุดสำรวจแยกจากการหยุด debug ถ้ามี defect ให้กลับไปตรวจ
พิกัดนั้นและแนบภาพ ไม่จำเป็นต้องรันทั่วโลกเพื่อยืนยันบั๊กหนึ่งจุด

ผ่าน Slice v1 เมื่อ technical gates ที่เกี่ยวข้องผ่าน และเจ้าของโปรเจกต์เดิน
route ครบตามเกณฑ์ 4 เสาหลัก พร้อมหลักฐาน ไม่มี blocker/major ค้าง
**ผ่าน slice ไม่ใช่ผ่านทั้งโลก**; M3 ต้องมีหลักฐานจากพื้นที่อิสระและ global
candidate ของตนเอง การปรับเป้าหมาย 10–15 นาทีหรือ 3 จุดค้นพบทำได้หลังทดลอง
แต่ต้องบันทึกเหตุผลและเกณฑ์ใหม่ก่อนรอบรับรอง ไม่ปรับย้อนหลังให้ผลเดิมผ่าน

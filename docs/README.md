# docs/

เป้าหมายและ scope ปัจจุบันอยู่ใน **[PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)**
งานปัจจุบันคือระบบน้ำ greenfield ใน `water_v2/`: ทำ compiler ให้ผ่านข้อมูลจริง
แล้วจึงเชื่อม writer ใหม่และตรวจในเกม ก่อนส่ง Immersion Slice v1 ซึ่งเป็น
เส้นทางเดินต่อเนื่อง 10–15 นาที พัฒนาและรัน local เท่านั้น

| ไฟล์ | เนื้อหา |
|---|---|
| [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md) | เป้าหมาย, ผล audit, scope, milestone และกติกาเลือกงาน |
| [ENVIRONMENT.md](ENVIRONMENT.md) | Python 3.11, dependency และวิธีใช้ run.ps1/preflight |
| [BASELINE.md](BASELINE.md) | เก็บ snapshot และรัน golden จาก code/input ชุดเดียวกัน |
| [WATER_V2.md](WATER_V2.md) | สัญญาของระบบน้ำใหม่, วิธี compile และขอบเขตที่ยังไม่รองรับ |
| [reviews/water_v2_20260914.md](reviews/water_v2_20260914.md) | ผลตรวจระบบน้ำใหม่และ gate ที่ยังไม่ผ่าน |
| [PAINT_TEST.md](PAINT_TEST.md) | แยกสถานะเซฟระบบเก่าจากระบบใหม่; เก็บวิธี build → paint → readback ของ candidate เก่า |
| [CHECK_IN_GAME.md](CHECK_IN_GAME.md) | ขั้นตอน world readback และรายการจุดตรวจย้อนหลัง |
| [IMMERSION_ROADMAP.md](IMMERSION_ROADMAP.md) | คิวงานถัดไปและ dependency ตาม milestone |
| [IMMERSION_REVIEW.md](IMMERSION_REVIEW.md) | สถานะ acceptance กลางและแบบบันทึกหลักฐาน |
| [PIPELINE.md](PIPELINE.md) | pipeline เดิมและส่วนสร้างโลกที่ยังใช้งาน; ระบบน้ำใหม่อ้าง WATER_V2 |
| [DECISIONS.md](DECISIONS.md) | บทเรียนและผลวัดเดิม; ไม่ใช่ข้อบังคับให้ rewrite คง logic น้ำเก่า |
| [IMMERSION_SPEC.md](IMMERSION_SPEC.md) | release contract: ต้องพิสูจน์อะไรจากโลกจริงก่อนเรียกว่า immersive |
| [TODO.md](TODO.md) | backlog และประวัติ implementation; ตัวเลขอาจเป็นของเก่า |

## ผังไดเรกทอรี (จัดใหม่ 2026-08-18)

| ที่ | เก็บอะไร |
|---|---|
| root | โค้ด + input/product ที่ pipeline รุ่นปัจจุบันยังอ้างด้วยชื่อคงที่ (`heightmap.png`, `landcover.npz`, `terrain_y.npy` ฯลฯ) |
| `water_v2/` | โค้ดระบบน้ำ greenfield แยกจาก shaper และ painter เดิม |
| `water_runs/` | product และ diagnostics ของระบบน้ำใหม่ แยกตาม run; local และไม่ track |
| `golden/` | ผลของ harness แต่ละ tag (git เก็บแค่ `final`) |
| `golden_patches/` | patch npz ที่ shape ไว้ใช้ซ้ำ |
| `logs/` | log ของการรันทั้งหมด |
| `previews/` | ภาพทดลอง/พรีวิวที่ไม่ใช่ input |
| `archive/history/redundant-root-patches-2026-09-10/` | สำเนา patch root ที่ SHA-256 ตรงกับ `golden_patches/` แล้วย้ายออกเพื่อไม่ให้สับสน |
| `archive/hydrology_runs/` | product `--global` ของระบบน้ำเก่า 22 ชุด (~31 GB); ไม่เป็น input หรือ baseline รับรอง `water_v2` |
| `archive/history/` | metric snapshot เก่า, ไฟล์ `.bak-newworld`, และ `manifests/` ของทุกรอบ global ที่ archive ไว้ (ยัง track ใน git) |
| `archive/baselines/` | สำเนา code/input/assets พร้อม SHA-256 สำหรับ baseline แต่ละรอบ (local, ไม่ track) |

## กติกาจัดเก็บไฟล์

- โค้ด pipeline และ input/product ที่สคริปต์ยังอ่านด้วยชื่อคงที่อยู่ที่ root จนกว่า
  จะมีการเปลี่ยน path contract แยกเป็นงานโดยเฉพาะ
- patch ระบบเดิมอยู่ใน `golden_patches/`; product ระบบใหม่อยู่ใน `water_runs/`
  แยกตาม run ห้ามวางสำเนาไว้ root
- ภาพ preview/diagnostic อยู่ใน `previews/` และ log อยู่ใน `logs/`; ผลที่ย้ายออก
  จาก root แต่ยังต้องเก็บอ้างอิงอยู่ใน `previews/legacy-root-2026-09-10/`
- หลักฐานเก่าที่ไม่ใช่ input ปัจจุบันอยู่ใต้ `archive/`; ห้ามใช้เป็น baseline
  โดยไม่ตรวจ fingerprint และวันที่
- virtual environments (`.venv`, `.venv311`) เป็นเครื่องมือ local ไม่ใช่ product
  และไม่ย้ายเข้าโฟลเดอร์ข้อมูล

## เริ่มเซสชันใหม่ให้อ่านตามนี้

1. `PROJECT_DIRECTION.md` และ `IMMERSION_REVIEW.md` — รู้เป้าหมายและ gate ที่ยังไม่ผ่าน
2. `IMMERSION_ROADMAP.md` — เลือกงานหลักหนึ่งเรื่องที่ช่วยปิด gate ปัจจุบัน
3. `WATER_V2.md` และรายงานระบบน้ำใหม่ — ตรวจสัญญาและข้อขัดแย้งที่กำลังแก้;
   ใช้ `PIPELINE.md`/`DECISIONS.md` เป็นบริบทของระบบเดิมที่เกี่ยวข้อง
4. ตรวจ provenance และ schema ของ product ก่อนใช้ ไม่ใช้ golden หรือผล
   readback ของระบบเก่ารับรองระบบใหม่; คำสั่ง compile ใหม่ยังไม่เขียนเซฟ
5. ทำ dev ก่อน รัน build/test ที่เกี่ยวข้องตอนท้ายรอบ รวม full suite เมื่อแก้โค้ด
   แล้วบันทึก pass/fail/skip จริง ห้ามถือว่า test ที่ fail/skip เป็นข้อยกเว้นถาวร

## บทเรียนที่แพงที่สุดในโปรเจกต์นี้

**อย่าเขียนค่าจากความจำเมื่อข้อมูลจริงอยู่ในเครื่อง** — schema ของ Minecraft 26.2
ต่างจากที่จำกันมาหลายจุด และการเดาทำให้โลกโหลดไม่ขึ้นมาแล้วหนึ่งครั้ง
อ่านจาก `26.2.jar` เสมอ (`make_world_datapack.find_client_jar()`)

**ผลลัพธ์ที่เท่ากันเป๊ะทุกค่าคือสัญญาณว่าพารามิเตอร์ไม่ถูกใช้จริง** — เจอสองครั้ง
(default argument ผูกค่าตอน import, และ metric ที่วัดผิดตัว)

**ตรวจด้วยภาพก่อนเชื่อ metric** — dither ผ่าน metric แต่ทำผิวทะเลสาบพังทั้งแผ่น
เห็นตอนเปิดภาพเทียบเท่านั้น

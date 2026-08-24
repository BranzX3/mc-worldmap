# docs/

| ไฟล์ | เนื้อหา |
|---|---|
| [PIPELINE.md](PIPELINE.md) | ลำดับการรัน + กฎ "แหล่งความจริงเดียว" + ไฟล์ที่สร้างระหว่างทาง |
| [DECISIONS.md](DECISIONS.md) | การตัดสินใจที่ผ่านการวัดแล้ว พร้อมตัวเลข — อ่านก่อนรื้ออะไร |
| [IMMERSION_SPEC.md](IMMERSION_SPEC.md) | release contract: ต้องพิสูจน์อะไรจากโลกจริงก่อนเรียกว่า immersive |
| [TODO.md](TODO.md) | งานที่เหลือ เรียงตามตัวเลขที่วัดได้ |

## ผังไดเรกทอรี (จัดใหม่ 2026-08-18)

| ที่ | เก็บอะไร |
|---|---|
| root | โค้ด + **input ที่สร้างใหม่ไม่ได้** (`heightmap.png`, `water_sources.npz`, `landcover.npz`) และ product ระดับโลกที่ pipeline ใช้อยู่ (`terrain_y.npy` ฯลฯ) |
| `golden/` | ผลของ harness แต่ละ tag (git เก็บแค่ `final`) |
| `golden_patches/` | patch npz ที่ shape ไว้ใช้ซ้ำ |
| `logs/` | log ของการรันทั้งหมด |
| `previews/` | ภาพทดลอง/พรีวิวที่ไม่ใช่ input |
| `archive/hydrology_runs/` | product `--global` ของรอบเก่า 22 ชุด (~31 GB) — **ล้าสมัยทั้งหมด** ตั้งแต่ shaper มี `section_id` สร้างใหม่ได้จาก `hydrology_shape.py --global` |
| `archive/history/` | metric snapshot เก่า, ไฟล์ `.bak-newworld`, และ `manifests/` ของทุกรอบ global ที่ archive ไว้ (ยัง track ใน git) |

## เริ่มเซสชันใหม่ให้อ่านตามนี้

1. `PIPELINE.md` — รู้ว่าอะไรต้องรันก่อนอะไร และห้ามคำนวณค่าซ้ำที่ไหน
2. `python report_metrics.py` — ตัวเลขปัจจุบัน (ตัวเลขใน TODO อาจเก่า)
3. `python -m unittest discover -s tests` — ควรผ่านทั้งหมดยกเว้น 1 ตัวที่รอผู้ใช้ตัดสิน
4. `TODO.md` — เลือกงาน

## บทเรียนที่แพงที่สุดในโปรเจกต์นี้

**อย่าเขียนค่าจากความจำเมื่อข้อมูลจริงอยู่ในเครื่อง** — schema ของ Minecraft 26.2
ต่างจากที่จำกันมาหลายจุด และการเดาทำให้โลกโหลดไม่ขึ้นมาแล้วหนึ่งครั้ง
อ่านจาก `26.2.jar` เสมอ (`make_world_datapack.find_client_jar()`)

**ผลลัพธ์ที่เท่ากันเป๊ะทุกค่าคือสัญญาณว่าพารามิเตอร์ไม่ถูกใช้จริง** — เจอสองครั้ง
(default argument ผูกค่าตอน import, และ metric ที่วัดผิดตัว)

**ตรวจด้วยภาพก่อนเชื่อ metric** — dither ผ่าน metric แต่ทำผิวทะเลสาบพังทั้งแผ่น
เห็นตอนเปิดภาพเทียบเท่านั้น

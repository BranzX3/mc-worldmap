# Baseline ที่ตรวจย้อนกลับได้

ใช้ `baseline_snapshot.py` เก็บ workspace ก่อนเริ่มแก้ morphology ในแต่ละรอบ
snapshot รวมโค้ดที่ยังไม่ commit, tests, config, requirements, เอกสาร,
input/product ที่ root และ tree assets พร้อม SHA-256 ทุกไฟล์
ไม่รวม save, virtual environment, cache หรือ personal settings

```powershell
.\run.ps1 baseline_snapshot.py capture baseline_YYYYMMDD
```

ผลอยู่ใน `archive/baselines/<tag>/`:

- `snapshot.json` — file hashes, Git HEAD/status และรุ่น package ที่ใช้
- `working-tree.patch` — binary diff ของ tracked files เทียบ HEAD
- `workspace/` — สำเนาไฟล์ครบ รวม untracked code และข้อมูลที่ต้องใช้

tag ใช้ได้ครั้งเดียว การเรียก capture ซ้ำจะหยุดก่อนเขียนทับ snapshot เดิม
สำเนาที่ยังไม่มี `snapshot.json` หมายถึง capture ไม่สำเร็จ ห้ามใช้รับรองผล
metadata เวลาของ input ถูกคงไว้ แต่การยืนยันเนื้อหาใช้ SHA-256

## รันจากสำเนาชุดที่ตรึงแล้ว

เรียกไฟล์จาก snapshot โดยใช้ Python 3.11 ของโปรเจกต์:

```powershell
.\run.ps1 archive/baselines/baseline_YYYYMMDD/workspace/golden_patches.py run --tag baseline_YYYYMMDD --force
```

`HERE` ของ generator/harness จะชี้เข้า snapshot จึงอ่าน input ชุดที่เก็บไว้
และสร้าง cache/results ภายใน snapshot ไม่ทับ cache ของ working tree
ใช้ `--force` เพื่อสร้างครบทุก patch ใหม่ ไม่อนุมานความสดจากชื่อ tag

สำหรับ suite ให้รันจาก `workspace/` ของ snapshot โดยเรียก Python executable
ที่บันทึกใน manifest โดยตรง เพื่อให้ทั้ง imports และ tests มาจากสำเนานั้น
`run.ps1` ปกติเปลี่ยน cwd กลับ root โปรเจกต์ จึงไม่ใช้เป็นตัวเรียก suite ในสำเนา

หลังรัน:

```powershell
.\run.ps1 baseline_snapshot.py verify baseline_YYYYMMDD --current
```

ตรวจว่า snapshot และ code/input/assets ปัจจุบันตรงกับ hashes ที่ capture
รวมตรวจ code/input ที่เพิ่มหรือลบ; เอกสาร live แก้ผลรีวิวได้โดยไม่ทำให้ gate นี้ล้ม
ไฟล์เอกสารใน snapshot ยังต้องไม่เปลี่ยน ผล generated เพิ่มใน snapshot ได้และ
ต้องเก็บ hash แยกในรายงานรับรอง

## เงื่อนไขรับ baseline

1. Snapshot verification ผ่านและ suite จากสำเนาเดียวกันผ่านด้วย dependency จริง
2. มี metrics และ fingerprints ครบทั้ง 11 patches จาก `--force`
3. รายงาน hard invariants และ morphology thresholds แยกกัน `run` exit 0
   เพียงอย่างเดียวไม่ได้แปลว่าคุณภาพผ่าน
4. เก็บ run/metrics, log, ภาพหน้าตัด และ npz ที่ใช้วัดไว้ผูกกับ snapshot
5. สรุปผลใน `docs/reviews/` พร้อมลิงก์จาก IMMERSION_REVIEW และอัปเดตงานถัดไป

การเทียบกับ `bank_final` เป็น historical comparison เท่านั้น เพราะ source
snapshot ของรอบเก่าไม่ครบ เริ่มการทดลองใหม่จาก baseline ปัจจุบันนี้แทน
การผ่าน M0 หมายถึงมีจุดอ้างอิงเชื่อถือได้ ไม่ได้บังคับให้ morphology ผ่านหมด
และไม่รับรอง world readback หรือ visual acceptance

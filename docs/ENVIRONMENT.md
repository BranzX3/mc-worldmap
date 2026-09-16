# Local development environment

Runtime หลักคือ **CPython 3.11 x64 ใน `.venv311`** ใช้ `run.ps1` จาก root
เพื่อให้ทุกคำสั่งเลือก interpreter เดียวกันและเปิด UTF-8 โดยไม่พึ่ง PATH/activation
`.venv` เดิมเป็น Python 3.13 และมี dependency คนละชุด เก็บไว้แต่ไม่ใช้กับ pipeline นี้

```powershell
.\run.ps1 preflight_environment.py --full
.\run.ps1 -m pip check
# ทำ dev ก่อน; ตรวจท้ายรอบ
.\run.ps1 -m unittest discover -s tests -v
```

คำสั่ง `python ...` ใน PIPELINE หมายถึง runtime นี้; บนเครื่องนี้ใช้
`.\run.ps1 ...` แทนคำว่า `python` คำสั่งทั้งหมดรัน local

## ติดตั้ง/ซ่อม dependency

บนเครื่องนี้ Python ต้นทางอยู่ที่
`C:\Users\User\AppData\Local\Programs\Python\Python311\python.exe`
และ interpreter ของโปรเจกต์อยู่ที่ `.venv311\Scripts\python.exe`
ถ้าเครื่องใหม่ยังไม่มี venv ให้สร้างด้วย Python 3.11 x64 ที่ติดตั้งจริง:

```powershell
& 'C:\Users\User\AppData\Local\Programs\Python\Python311\python.exe' -m venv .venv311
.\run.ps1 -m pip install -r requirements.txt
```

ไม่ต้องสร้าง venv ทับของเดิมที่ทำงานอยู่ `requirements-runtime.txt` ตรึง
dependency หลักของ build/paint (NumPy 1.26.4, SciPy 1.17.1, Amulet 1.9.42,
NBT 2.1.8, Pillow 12.3.0, PyMCTranslate 1.2.45); `requirements.txt` เพิ่ม GIS
ที่ใช้ดึงข้อมูลและทดสอบ fetch-landcover รุ่นเหล่านี้เป็นชุดที่ตรวจบนเครื่องนี้
ยังไม่ได้อ้างว่าเป็น lockfile ของ dependency ทางอ้อมทุกตัว

## สิทธิ์เข้าถึงและไฟล์เกม

การตรวจ 2026-09-10 ยืนยันว่า `.venv311` เปิดไม่ได้ภายใน sandbox บางขอบเขต
แต่เปิดได้ตามปกติเมื่ออนุญาตให้รันนอก sandbox ไม่ใช่ Python หายหรือ venv เสีย
หากขึ้น `Unable to create process` ให้ตรวจสิทธิ์ของ Python ต้นทางก่อนติดตั้งใหม่
หาก Codex ถูกจำกัดการอ่าน jar/save ให้ใช้การขอสิทธิ์ของเครื่องมือสำหรับคำสั่งนั้น
ไม่เปลี่ยน ACL ไม่ลบ `session.lock` และไม่สลับไป Python 3.13 เพื่อหลบอาการ

`make_world_datapack.find_client_jar()` ค้นทั้ง Modrinth และ `.minecraft`
รองรับชื่อโฟลเดอร์รวม loader เช่น `26.2-0.19.3` โดยชื่อ jar ตรงกับโฟลเดอร์
เลือก jar ที่แก้ไขล่าสุดตามพฤติกรรมเดิม ตรวจ `client_jar.path/version` ใน preflight
ให้ตรงเกมที่ใช้ ถ้าต้องตรึงเฉพาะไฟล์ ให้ตั้ง `CLIENT_JAR` ใน `config.py`
พาธที่ตรึงผิดจะหยุดโดยไม่เลือกเกมเวอร์ชันอื่นแทนเงียบ ๆ

ผลตรวจครั้งนี้เลือก `%APPDATA%\.minecraft\versions\26.2\26.2.jar`
และอ่าน schema เกม 26.2 / data pack major 107 ได้จริง
save ยังคงเป็น `config.WORLD_PATH` (`mmotest` ในโปรไฟล์ Fabulously Optimized)
overworld ของ save นี้อยู่ที่ `dimensions/minecraft/overworld/region`
ไม่ใช่ `region` ที่ราก save

## ความหมายของ preflight

`--full` ตรวจ dependency GIS เพิ่มจาก runtime หลัก รายงานเป็น JSON พร้อม
interpreter, version, ผล import จริงและ error ของแต่ละ dependency, schema jar,
session lock และผลเปิด handle อ่าน/เขียนของ `level.dat` กับ region ตัวอย่างหนึ่งไฟล์
probe เปิดแล้วปิดโดยไม่เปลี่ยน bytes และรองรับทั้ง dimension layout กับ legacy layout

`ready: true` หมายถึง environment พร้อมสำหรับขั้นตอนถัดไป ณ เวลาตรวจ
ไม่ใช่ผลรับรอง geometry, ทุก region ACL, datapack ที่เปิดใช้ หรือ immersion
`world_session_locked: true` อาจเกิดจากเกมล็อกไฟล์หรือสิทธิ์ไม่พอ ต้องอ่าน
`world_access.error` ร่วมด้วย เกมต้องปิดก่อน build/paint และ writer จะตรวจ lock ซ้ำ
world readback/build/golden เป็นงาน baseline ถัดไปตาม roadmap

หลักฐานรอบ environment อยู่ใน `logs/environment-preflight-2026-09-10.json`
และ `logs/environment-tests-2026-09-10.log` (local, ไม่ track ใน git)
ผลสรุปที่ track อยู่ใน [IMMERSION_REVIEW.md](IMMERSION_REVIEW.md)

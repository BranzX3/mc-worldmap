# mc-worldmap

สร้างภูมิประเทศ Salzkammergut ใน Minecraft สำหรับ MMORPG ให้ผู้เล่นอ่านพื้นที่ออก
เดินสำรวจได้ และอยากไปต่อจากสิ่งที่เห็นในโลก พัฒนาและรันบนเครื่อง local เท่านั้น

**งานปัจจุบัน:** เขียนระบบน้ำใหม่แบบ greenfield ใน `water_v2/` จาก DEM และ
OSM โดยแยกจาก logic น้ำเดิม ต้องผ่านข้อมูลจริงและการตรวจในเซฟก่อนนำไปใช้
กับ **Immersion Slice v1** — เส้นทางเดินต่อเนื่อง 10–15 นาที

ระบบใหม่ยังเป็น compiler ของ scene ที่มีขอบเขตจำกัด ข้อมูลจริงยังมีข้อขัดแย้ง
ที่ต้องแก้ และยังไม่ได้เขียนลงเซฟ ดู [ระบบน้ำใหม่](docs/WATER_V2.md) และ
[ผลตรวจรอบปัจจุบัน](docs/reviews/water_v2_20260914.md)

เริ่มจาก [ทิศทางและขอบเขต](docs/PROJECT_DIRECTION.md),
[คิวงานพัฒนา](docs/IMMERSION_ROADMAP.md) และ
[สถานะ acceptance](docs/IMMERSION_REVIEW.md)
ดู [คู่มือเอกสาร](docs/README.md) สำหรับ pipeline, technical spec และ backlog

Runtime: Python 3.11 ใน `.venv311`; ใช้ `.\run.ps1 preflight_environment.py --full`
เพื่อตรวจความพร้อม ดู [คู่มือ environment](docs/ENVIRONMENT.md)

ขนาดพื้นที่จริง 40 × 40 km, 4 เมตรต่อบล็อกทั้งแนวนอนและแนวตั้ง
ข้อมูลและโลกสร้างอยู่ในเครื่อง; ขั้นตอนและเซฟทดลองอยู่ใน [PAINT_TEST](docs/PAINT_TEST.md)
ผลเก่าและฟีเจอร์ที่เขียนโค้ดแล้วไม่ถือเป็นหลักฐานว่า immersion ผ่าน

# Immersion acceptance spec

ทิศทางและขอบเขตปัจจุบันอ้าง [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)
เกณฑ์ในฉบับนี้เป็น technical contract; การผ่าน Slice v1 ต้องผ่าน route และ
player review ตามทิศทางหลักด้วย สถานะรับรองปัจจุบันอยู่ใน
[IMMERSION_REVIEW.md](IMMERSION_REVIEW.md) ตัวเลขย้อนหลังด้านล่างเป็นหลักฐาน
ของรอบที่ระบุ ไม่รับรอง working tree ปัจจุบัน

เป้าหมายของโปรเจกต์ไม่ใช่แค่สร้างโลกที่โหลดได้หรือไม่มีน้ำรั่ว แต่ต้องทำให้ผู้เล่น
เดินอยู่ใน Salzkammergut แล้วอ่านภูมิประเทศออกว่าเป็นสถานที่จริงที่มีเหตุผล:
ลำน้ำต้องพาไปสู่ที่ต่ำ ตลิ่งต้องเดินสำรวจได้ วัสดุต้องตอบสนองต่อแรงน้ำและธรณี
ป่ากับทุ่งต้องเปลี่ยนอย่างค่อยเป็นค่อยไป และต้องมีพื้นที่ให้เข้าไปค้นพบ ไม่ใช่
เป็นเพียง heightmap ที่ทาสีสวยจากด้านบน

เอกสารนี้เป็น release contract ส่วน `TODO.md` เป็นรายการ implementation งานหนึ่ง
จะถือว่าเสร็จเมื่อผ่านหลักฐานที่ระบุที่นี่ ไม่ใช่เมื่อโค้ดหรือ metric ตัวเดียวผ่าน

## หลักฐานสองด้านที่ต้องผ่านร่วมกัน

**ความถูกต้อง:** บล็อกที่อ่านกลับจาก region file หลัง save/reopen ยืนยันว่าสิ่งที่
เขียนตรงกับ product; full-map verifier ตรวจ invariant ของ product ทั้งพื้นที่;
golden patches ตรึงเคสยากและเปรียบเทียบก่อน/หลัง ทั้งหมดต้องผูกกับ snapshot เดียวกัน

**ประสบการณ์:** การเดินจริงและภาพจากบล็อกโลกจริงระดับสายตาตัดสิน scale,
repetition, การอ่านทาง และความน่าสำรวจ ไม่ลดเป็นหลักฐานชั้นรองของตัวเลข
array metric/preview ใช้สร้างสมมติฐาน ไม่ใช้ยืนยันโลกจริงลำพัง และ readback
อย่างเดียวก็ไม่ยืนยันว่าผู้เล่นรู้สึก immersive

`audit_global.py` เป็นการสุ่ม จึงใช้หาปัญหาได้แต่ใช้พิสูจน์ว่า invariant ทั้งโลก
เป็นศูนย์ไม่ได้ ส่วน `render_view.py` ปัจจุบันยังใช้ผิวสังเคราะห์ และภาพจากจุดที่
ขึ้น camera-site warning ถูกผาใกล้กล้องบัง จึงยังไม่ใช่ visual release gate

## Definition of confidence ก่อนแก้โค้ด

ทุกการแก้ต้องมีครบตามลำดับนี้:

1. มี artifact ที่วัดจาก input หรือโลกจริง ไม่ใช่ข้อสันนิษฐานจากภาพจำ
2. มีคำอธิบาย root cause ที่ทำให้สร้างอาการเดิมซ้ำได้
3. มี test หรือ metric ที่ล้มก่อนแก้และผ่านหลังแก้ โดยวัด artifact เดียวกัน
4. golden patches ไม่ถอยหลังใน hard invariants
5. ถ้าแตะ painter ต้อง paint patch ตัวแทนแล้วอ่าน region file กลับ
6. รัน test suite เต็มตอนจบรอบงาน

ถ้าขาดข้อใดข้อหนึ่ง งานนั้นยังเป็นการทดลองและห้ามเปิดเป็น default

## Acceptance matrix

### 1. ความถูกต้องและความปลอดภัย — hard gate

| Requirement | หลักฐานที่ใช้ | เกณฑ์ผ่าน |
|---|---|---|
| น้ำไม่รั่ว/ไม่ลอย/ไม่มีช่องว่าง | full-map verifier | ทุก invariant = 0 ทั้งแผนที่ |
| หน้าตัดเดียวกันมีระดับเดียวกัน | section-aware global verifier | 0 run ที่ผิด |
| รอยต่อ tile ไม่สร้าง artifact ใหม่ | full-map seam comparison | seam ไม่แย่กว่าภายในเกิน noise ที่ตรึงไว้ |
| สิ่งที่ paint ตรงกับ product | world readback ด้วย root+patch ชุดเดียวกัน | missing/extra column = 0; bed และ top ตรง |
| ฟีเจอร์ทดลองไม่ทำลายโลก | unit + world readback | test ผ่านก่อนเปิด default |

สถานะปัจจุบัน: golden patches ผ่าน hard invariant และ `verify_hydrology.py
hydrology_global3` ลดทะเลสาบลอยเหนือลำน้ำ 64 -> 0 cells โดย invariant อื่นยัง
เป็นศูนย์ทั้งหมด อย่างไรก็ดี golden compare พบ `terrain_lifted_cells` ถอยหลัง
4 patch จึงผ่าน correctness แต่ยังไม่ผ่าน morphology ส่วน rock shelters ผ่าน
unit safety 10/10 แล้วแต่ยังปิดเป็น default จนกว่าจะมี world readback ของโพรง

### 2. น้ำที่เดินสำรวจได้ — playability gate

| Requirement | หลักฐานที่ใช้ | เกณฑ์เริ่มต้น |
|---|---|---|
| เดินเลียบลำน้ำทั่วไปได้ | bank climb จาก geometry จริง | climb >4 ต้องเป็น feature ที่ประกาศ เช่น gorge/waterfall |
| ตลิ่งไม่กลายเป็นกำแพงต่อเนื่อง | `bank_unwalkable_excess` + connected runs | patch ทั่วไป <=5%; run ยาวต้องมีทางลงเป็นช่วง |
| ลำน้ำไม่ขุดหุบขึ้นมาเอง | canyon excess เทียบ DEM | <=5% นอก gorge ที่ประกาศ |
| ปากน้ำต่อเนื่อง | lake-mouth patch + world readback | ไม่มี step/ม่านที่ไม่ได้ประกาศเป็น waterfall |
| น้ำตกอ่านเป็น feature | lip + curtain + pool + downstream reach | ต้องมีครบทั้งกลุ่ม ห้ามเป็นเสาเดี่ยว |

ผล golden ล่าสุดหลังเพิ่ม final seal cap และ bank smoothing (`bank_final`,
2026-08-25): `bank_unwalkable_excess` ลดเหลือ `prototype_stream` -0.5%,
`player_liked` -0.4%, `cliff_bedding` 3.1%, `steep_stream` 3.8% และ
`hill_junction` 5.1% (ใกล้เกณฑ์ 5% แต่ยังเกินเล็กน้อย); ไม่มี golden patch ใด
แย่ลง และ hard invariant ทั้งหมดเป็นศูนย์. climb สูงสุด 9–10 ยังอยู่ในจุดที่
เป็นหน้าผา/feature ของภูมิประเทศจริง จึงยังไม่ควรเปิด threshold แบบเหมารวม

รอบตรวจ 2026-08-20 พบ root cause ย่อยอีกชั้น: การ seal ชายฝั่งจากหน้าตัดที่ทับกัน
เคยสะสม `SEAL_MAX_RAISE` ต่อรอบจน synthetic ยก +6; ตอนนี้มีเพดานต่อ cell จาก DEM
เดิมและมี regression test แล้ว แต่ golden metrics ไม่เปลี่ยน เพราะปัญหาหลักที่เหลือ
อยู่ใน final water-edge seal/หน้าผา จึงยังไม่ลดตัวเลขด้วยการแก้ safety นี้ และยังไม่
เปิด threshold ใหม่จนกว่าจะแยก bank ที่เป็น waterfall feature ออกจาก ordinary bank
ด้วย artifact เดียวกัน. รอบ 2026-08-25 เพิ่ม post-pass ที่ลดเฉพาะ dry bank
ที่ติดน้ำและปีนเกิน 1 บล็อก โดยไม่ยก cell และเว้น waterfall lip/top; มี unit
regression สำหรับทั้งการไม่สร้างคันดินและการไม่แตะ waterfall feature แล้ว

### 3. น้ำที่ดูมีเหตุผล — visual/ecological gate

กฎ “น้ำแรงไม่มีตะกอนละเอียด” เป็นเงื่อนไขจำเป็นแต่ไม่พอ วัสดุต้องมีทั้งความ
ถูกต้องทางกระบวนการและความหลากหลายเชิงพื้นที่:

- วัสดุหนึ่งชนิดห้ามครอง reach ยาวเพราะ threshold ของ noise ไม่ครอบช่วงจริง
- patch วัสดุต้องต่อเนื่องเป็น riffle, bar, exposed bedrock หรือ deposit ไม่ใช่
  salt-and-pepper และไม่ใช่แถบ palette เดียวตลอดสาย
- การเปลี่ยน stream -> lake ต้องค่อยเปลี่ยนตามแรงน้ำ ความลึก และ exposure
- พืชน้ำต้องอยู่ในกระแสที่ยึดเกาะได้ และต้องไม่แทนพื้นที่น้ำจนปิดทางอ่านผิวน้ำ
- ทะเลสาบต้องมี wind exposure/fetch เพื่อแยกฝั่งรับคลื่นกับอ่าวสงบ
  (`water_ecology.lake_fetch` คุมตะกอนและพืชน้ำแล้ว; ต้องยืนยันด้วย paint/readback)

ยังไม่ตั้งเปอร์เซ็นต์ diversity เป็น hard gate จนกว่าจะวัด connected material
patches และเทียบหลาย reach เพราะการบังคับ entropy โดยไม่มี spatial context จะสร้าง
ลายสุ่มที่ดูแย่กว่าเดิม

### 4. Landscape coherence

- ขอบป่าต้องไล่จาก canopy -> ต้นเล็ก -> scrub -> meadow ไม่ตัดเป็นเส้น
  (`surface.forest_ecotone_mask` ทำ scrub transition สองบล็อก และ
  `forest_interior_factor` ลด emergent/canopy แต่คง understory ตรงขอบแล้ว;
  ยังรอ world gate)
- forest stand ต้องมีอย่างน้อย canopy, understory, gap และ deadwood ที่สัมพันธ์กัน
- meadow ต้องแยก dry/wet/pasture ตามความชื้น ความชัน และ disturbance
  (`vegetation.meadow_disturbance` ทำสนาม disturbance ต่อเนื่องและใช้ร่วมกันทั้ง
  pasture cover กับหย่อม coarse dirt/dirt แล้ว; ยังรอ world gate)
- สันเขาสูงต้องมี arête/rock exposure; glacier ต้องมี rock window, crevasse และ
  moraine แทน packed ice ผืนเดียว (`glacier_rock_window_mask` ทำ rock window แล้ว;
  `--glacier-detail` ทำ crevasse/moraine แบบ opt-in แล้ว แต่ยังรอ world gate)
- สี biome, fog, snow และ vegetation ต้องเปลี่ยนตามระดับสูงโดยไม่มี band แข็ง

### 5. Exploration density

โลก 2.5D ที่ตันทั้งใบไม่ผ่าน immersion แม้ผิวสวย ต้องมีจุดให้ค้นพบในระยะเดิน:

- rock shelter ที่มองเห็นและเข้าได้จากด้านนอก โดยไม่รั่วน้ำหรือทะลุผิว
- Slice v1 จำกัดที่กำบัง/โพรงระดับผิวตาม PROJECT_DIRECTION; ระบบถ้ำใต้ดินยาว
  อยู่นอก milestone แรกโดยตั้งใจ
- Slice v1 ต้องมี route ที่เดินได้และจุดค้นพบต่างบทบาทอย่างน้อย 3 จุด ใช้
  crossing, landmark, rest point ตามความจำเป็น; pipeline สำหรับทั้งโลก
  ออกแบบหลังพิสูจน์ slice ไม่รวม quest/economy/lifeskill ใน acceptance นี้

## World-paint evidence — 2026-08-18 (latest accessible readback)

paint ใหม่ด้วย golden patch ปัจจุบันหลังสร้าง `hydrology_global3`; shelters ยัง
เป็น opt-in (`--shelters`) และอ่าน region file กลับจากโลก `mmotest` เมื่อ
environment อนุญาต:

การเปลี่ยนแปลงหลัง readback ชุดนี้ (bank smoothing, seal-notch restore, lake fetch,
ecotone/glacier windows, shelters opt-in และ atomic tree placement) มี unit และ
patch-array evidence แล้ว; golden rerun เต็มชุดและ world readback รอบใหม่ยังขาด
เพราะ environment ปัจจุบันปฏิเสธการอ่าน region files ของ save นี้ จึงห้ามนับว่า
เป็น visual acceptance จนกว่าจะอ่านกลับได้

| Patch | คอลัมน์น้ำ | Geometry readback | วัสดุก้นน้ำที่เด่น |
|---|---:|---|---|
| wild_canyon | 2,153 | bed/top ตรง; ไม่มีน้ำเกิน; source water ทั้งหมด | cobble 59%, stone 28%, sand 7%, gravel 7%; same-neighbour 88.2% |
| steep_stream | 1,928 | bed/top ตรง; max run 8/8 | cobble 71%, stone 29%; same-neighbour 86.8% |
| lake_mouth | 5,042 | bed/top ตรง; max run 5/5 | stream sand 89%, gravel 12% (26 cells); lake gravel 52%, cobble 41%; same-neighbour 96%/94% |

ตำแหน่งที่ water block ถูกแทนด้วย seagrass/tall seagrass นับเป็น fluid occupancy
ไม่ใช่น้ำหาย: wild_canyon 14 คอลัมน์ และ lake_mouth 162 คอลัมน์ หลังแยกกรณีนี้
ไม่พบ geometry mismatch ในสาม patch

ข้อสรุปจากหลักฐานรอบนี้: writer ทำ geometry ลงโลกตรงกับ patch และ fine coherent
texture ลดการซ้ำของวัสดุใน wild/steep ได้จริงโดยไม่ทำให้ golden geometry ถอย
แต่ lake-mouth stream ยังเป็น deposit เดียวเพราะมีเพียง 26 cells ปัญหารอบถัดไป
จึงต้องแก้ sediment/deposit morphology และ bank walkability ไม่ใช่เพิ่ม noise ต่อ

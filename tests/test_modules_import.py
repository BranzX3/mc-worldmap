"""ทุกสคริปต์ต้อง import ได้ — ด่านที่ถูกกว่าการรู้ตอนรันจริงไป 45 นาที

`audit_global.py` เคยถูกคอมมิตในสภาพที่ SyntaxError (f-string ขาดปิด) แล้วไม่มี
อะไรฟ้อง เพราะไม่มีเทสต์ไหน import มันเลย กว่าจะรู้ก็ตอนเรียกใช้หลังรัน --global
เสร็จ  เทสต์นี้ไม่ตรวจพฤติกรรม แค่ยืนยันว่าไฟล์ยัง parse ได้และ import สำเร็จ
โดยไม่ต้องมี product ใด ๆ อยู่ในเครื่อง

โมดูลที่ต้องเปิด world หรืออ่านไฟล์ขนาดใหญ่ตอน import ไม่มีอยู่ในโปรเจกต์นี้ —
ทุกตัวทำงานหนักใน main() เท่านั้น ถ้าวันหนึ่งมีตัวที่ทำไม่ได้ ให้ใส่ชื่อไว้ใน
`NEEDS_DATA` พร้อมเหตุผล ไม่ใช่ปิดเทสต์ทั้งตัว
"""
import importlib
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# โมดูลที่ import แล้วต้องมีไฟล์ข้อมูลอยู่จริง — ข้ามได้เฉพาะตัวที่มีเหตุผล
NEEDS_DATA = {
    # ต้องมี rasterio + dem.tif ซึ่งไม่มีอยู่ในเครื่องแล้ว (ดู PIPELINE.md)
    "make_heightmap": "ต้องใช้ rasterio และ dem.tif ที่หายไปแล้ว",
    # ทำงานจริงตั้งแต่ตอน import: อ่าน sys.argv แล้วเปิดโลก Minecraft
    # ควรย้ายเข้า main() แต่เป็นสคริปต์ตรวจมือ ไม่ได้อยู่ใน pipeline
    "scan_entities": "เปิดโลกตั้งแต่ตอน import — ต้องแยกเข้า main() ก่อน",
    "inspect_entities": "อ่าน sys.argv ตั้งแต่ตอน import — ต้องแยกเข้า main() ก่อน",
    "inspect_region": "อ่าน sys.argv ตั้งแต่ตอน import — ต้องแยกเข้า main() ก่อน",
    # สคริปต์ดึงข้อมูลรอบแรก ใช้ครั้งเดียวตอนตั้งโปรเจกต์ และ geopandas ไม่ได้
    # อยู่ใน requirements ของเครื่องนี้ (ข้อมูล OSM ถูกดึงมาแล้ว)
    "fetch_landcover": "ต้องใช้ geopandas",
    "fetch_map": "ต้องใช้ geopandas",
}


def module_names():
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        yield name[:-3]


class ModuleImportTests(unittest.TestCase):
    def test_every_top_level_script_imports(self):
        failures = []
        for name in module_names():
            if name in NEEDS_DATA:
                continue
            try:
                importlib.import_module(name)
            except Exception as error:            # pragma: no cover - รายงาน
                failures.append(f"{name}: {type(error).__name__}: {error}")

        self.assertEqual(failures, [], "import ไม่ผ่าน:\n  " + "\n  ".join(failures))


if __name__ == "__main__":
    unittest.main()

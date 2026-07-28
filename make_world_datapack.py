"""สร้าง datapack ขยายความสูงของ overworld ให้ตรงกับ config.py

ทำไมต้องมี: ช่วงความสูงจริงของพื้นที่นี้คือ 2557 m ที่ 4 m/block (สัดส่วนไม่
บิดเบือน) ต้องใช้ 640 บล็อก ซึ่งเกินเพดานวานิลลา 384 บล็อก การขยายความสูงของ
dimension เป็นฟีเจอร์วานิลลาตั้งแต่ 1.17 ทำได้ด้วย datapack ไม่ต้องมอด และ
amulet อ่าน min_y/height จาก datapack นี้เพื่อกำหนดขอบเขตที่ยอมให้เขียน

ต้องรัน *ก่อน* build_terrain.py และต้องรันบนโลกที่ยังไม่มีข้อมูล chunk
(ขยายความสูงของโลกที่มี chunk อยู่แล้วจะทำให้ข้อมูลเดิมเพี้ยน)

ใช้:
    python make_world_datapack.py            # เขียนลงโลกใน config.WORLD_PATH
    python make_world_datapack.py --check     # ตรวจว่าตรงกับ config ไหม ไม่เขียน

หมายเหตุ: pack_format ต้องตรงกับเวอร์ชันเกม ค่าเริ่มต้นในไฟล์นี้ตั้งไว้กว้าง
ด้วย supported_formats หากเกมไม่ยอมโหลด ให้แก้ PACK_FORMAT ตามเวอร์ชันที่ใช้
"""

import json
import os
import sys

import config as C

HERE = os.path.dirname(os.path.abspath(__file__))
PACK_NAME = "tall_overworld"

def find_client_jar():
    """หาไฟล์ jar ของเกมเพื่ออ่าน schema จริง

    เหตุผลที่ต้องอ่านจากเกม ไม่ใช่เขียนจากความจำ: ครั้งแรกที่ทำผมประกอบ
    dimension_type เองแล้วโลกโหลดไม่ขึ้น เพราะ schema ของ 26.2 ต่างจากที่จำไว้
    (monster_spawn_light_level ไม่มี "value" ซ้อน, มี has_ender_dragon_fight,
    ฟิลด์อย่าง ultrawarm/natural/bed_works ย้ายไปอยู่ใน attributes หมดแล้ว)
    """
    override = getattr(C, "CLIENT_JAR", None)
    if override and os.path.isfile(override):
        return override
    roots = [
        os.path.join(
            os.environ.get("APPDATA", ""), "ModrinthApp", "meta", "versions"
        ),
        os.path.join(os.environ.get("APPDATA", ""), ".minecraft", "versions"),
    ]
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            jar = os.path.join(root, name, f"{name}.jar")
            if os.path.isfile(jar) and os.path.getsize(jar) > 5_000_000:
                found.append(jar)
    if not found:
        raise SystemExit(
            "หา jar ของเกมไม่เจอ — ตั้ง CLIENT_JAR ใน config.py ให้ชี้ไฟล์ .jar "
            "ของเวอร์ชันที่ใช้ (ต้องใช้อ่าน schema จริงของ dimension_type)"
        )
    return max(found, key=os.path.getmtime)


def _read_json_from_jar(path_in_jar):
    import zipfile

    with zipfile.ZipFile(find_client_jar()) as z:
        return json.loads(z.read(path_in_jar))


def pack_format():
    """pack_format ของเวอร์ชันนี้ อ่านจาก version.json ในตัวเกม"""
    version = _read_json_from_jar("version.json")
    packs = version.get("pack_version", {})
    return int(packs["data_major"]), version.get("id", "?")


def dimension_type():
    """overworld ของวานิลลา แก้เฉพาะความสูงและระดับเมฆ

    override เป็นการแทนที่ทั้งก้อน ไม่ใช่ merge จึงต้องเริ่มจากของจริงทั้งชุด
    แล้วแก้เท่าที่จำเป็น ไม่งั้นฟิลด์ที่ขาดจะทำให้โลกโหลดไม่ขึ้น
    """
    dim = _read_json_from_jar("data/minecraft/dimension_type/overworld.json")
    dim["min_y"] = int(C.WORLD_Y_MIN)
    dim["height"] = int(C.WORLD_HEIGHT)
    dim["logical_height"] = int(C.WORLD_HEIGHT)

    attributes = dim.setdefault("attributes", {})
    # วานิลลาวางเมฆไว้ y=192 สำหรับโลกสูง 384 ภูเขาของเราสูงถึง y=640 ถ้าไม่ยก
    # เมฆจะลอยตัดกลางเทือกเขาทั้งแผนที่
    attributes["minecraft:visual/cloud_height"] = float(
        getattr(C, "CLOUD_HEIGHT", C.Y_TERRAIN_MAX + 40)
    )
    fog = getattr(C, "FOG_COLOR", None)
    if fog:
        attributes["minecraft:visual/fog_color"] = fog
    sky = getattr(C, "SKY_COLOR", None)
    if sky:
        attributes["minecraft:visual/sky_color"] = sky
    return dim


def pack_mcmeta():
    fmt, version_id = pack_format()
    return {
        "pack": {
            "pack_format": fmt,
            # ตั้งแต่ pack_format 81 ขึ้นไป เกมบังคับสองฟิลด์นี้แทน
            # supported_formats แบบเดิม
            "min_format": fmt,
            "max_format": fmt,
            "description": (
                f"Salzkammergut — overworld y {C.WORLD_Y_MIN}..{C.WORLD_Y_MAX} "
                f"({C.WORLD_HEIGHT} blocks), 4 m/block  [MC {version_id}]"
            ),
        }
    }


def validate_config():
    """ตรวจกฎที่วานิลลาบังคับ ก่อนเขียนไฟล์ที่เกมจะปฏิเสธ"""
    errors = []
    if C.WORLD_HEIGHT % 16:
        errors.append(f"WORLD_HEIGHT ({C.WORLD_HEIGHT}) ต้องเป็นพหุคูณของ 16")
    if C.WORLD_Y_MIN % 16:
        errors.append(f"WORLD_Y_MIN ({C.WORLD_Y_MIN}) ต้องเป็นพหุคูณของ 16")
    if not (16 <= C.WORLD_HEIGHT <= 4096):
        errors.append(f"WORLD_HEIGHT ({C.WORLD_HEIGHT}) ต้องอยู่ใน 16..4096")
    if C.WORLD_Y_MIN < -2032:
        errors.append(f"WORLD_Y_MIN ({C.WORLD_Y_MIN}) ต้อง >= -2032")
    if C.WORLD_Y_MIN + C.WORLD_HEIGHT > 2032:
        errors.append(
            f"min_y + height ({C.WORLD_Y_MIN + C.WORLD_HEIGHT}) ต้อง <= 2032"
        )
    # ภูมิประเทศต้องอยู่ในกรอบ และเหลือหัวให้ต้นไม้/หิมะ
    if C.Y_TERRAIN_MIN < C.WORLD_Y_MIN:
        errors.append("Y_TERRAIN_MIN ต่ำกว่าพื้นโลก")
    if C.Y_TERRAIN_MAX > C.Y_BUILD_CEILING:
        errors.append(
            f"Y_TERRAIN_MAX ({C.Y_TERRAIN_MAX}) สูงกว่าเพดานที่เขียนได้ "
            f"({C.Y_BUILD_CEILING})"
        )
    if C.Y_FILL_BOTTOM < C.WORLD_Y_MIN:
        errors.append("Y_FILL_BOTTOM ต่ำกว่าพื้นโลก")
    headroom = C.Y_BUILD_CEILING - C.Y_TERRAIN_MAX
    if headroom < 48:
        errors.append(
            f"เหลือหัวเหนือยอดเขาแค่ {headroom} บล็อก ต้องมากกว่าต้นไม้ที่สูงสุด "
            "ในแพ็ก (48)"
        )
    return errors


def paths():
    root = os.path.join(C.WORLD_PATH, "datapacks", PACK_NAME)
    return {
        "root": root,
        "mcmeta": os.path.join(root, "pack.mcmeta"),
        "dimension": os.path.join(
            root, "data", "minecraft", "dimension_type", "overworld.json"
        ),
    }


def write_biomes(root):
    """เขียน biome ที่ออกแบบเอง — สคริปต์ paint อ้างถึงชื่อพวกนี้ตรง ๆ

    ถ้า datapack ไม่ถูกโหลด เกมจะไม่รู้จัก biome เหล่านี้และ chunk จะแสดงสีผิด
    หรือโหลดไม่ขึ้น จึงต้องยืนยันด้วย /datapack list ทุกครั้งหลังติดตั้ง
    """
    import biomes as B

    written = 0
    for rel, payload in B.datapack_files().items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        written += 1
    return written


def enable_in_level_dat(world_path):
    """เพิ่ม file/<pack> เข้า Data.DataPacks.Enabled ของ level.dat

    จำเป็นเพราะ amulet อ่าน datapack เฉพาะที่อยู่ในรายการนี้ ([format.py] data_pack)
    การวางโฟลเดอร์ใน datapacks/ เฉย ๆ ไม่พอ — amulet จะใช้ความสูงดีฟอลต์
    -64..320 แล้วปฏิเสธการเขียนทุกอย่างเหนือ y=319 เงียบ ๆ

    ปกติ Minecraft เขียนรายการนี้ให้เองตอนเปิดโลกครั้งแรกหลังใส่ datapack
    คำสั่งนี้มีไว้สำหรับกรณีที่ยังไม่ได้เปิดเกม หรืออยากยืนยันก่อนรันงานยาว
    """
    import amulet_nbt

    entry = f"file/{PACK_NAME}"
    level_dat = os.path.join(world_path, "level.dat")
    if not os.path.isfile(level_dat):
        raise SystemExit(f"ไม่พบ level.dat ที่ {world_path}")

    tag = amulet_nbt.load(level_dat)
    data = tag.compound.get_compound("Data")
    packs = data.setdefault_compound("DataPacks", amulet_nbt.CompoundTag())
    enabled = packs.setdefault_list("Enabled", amulet_nbt.ListTag())
    if any(
        isinstance(v, amulet_nbt.StringTag) and v.py_str == entry for v in enabled
    ):
        return False

    backup = level_dat + ".before_tall_overworld"
    if not os.path.exists(backup):
        with open(level_dat, "rb") as src, open(backup, "wb") as dst:
            dst.write(src.read())
    enabled.append(amulet_nbt.StringTag(entry))
    tag.save_to(level_dat, compressed=True)
    return True


def verify_world_bounds():
    """เปิดโลกด้วย amulet แล้วเทียบขอบเขตจริงกับ config

    ต้องรันก่อนงานยาวทุกครั้ง — ถ้า amulet มองเห็นความสูงผิด build_terrain จะ
    เขียนได้แค่ถึง y=319 และภูเขาครึ่งบนจะหายไปโดยไม่มี error
    """
    import amulet

    level = amulet.load_level(C.WORLD_PATH)
    try:
        box = level.bounds(C.DIMENSION).selection_boxes[0]
        low, high = int(box.min_y), int(box.max_y)
    finally:
        level.close()

    want_low, want_high = C.WORLD_Y_MIN, C.WORLD_Y_MIN + C.WORLD_HEIGHT
    ok = (low, high) == (want_low, want_high)
    print(f"amulet เห็นขอบเขต y {low}..{high} | config ต้องการ {want_low}..{want_high}")
    if not ok:
        raise SystemExit(
            "ขอบเขตไม่ตรง — amulet จะตัดการเขียนที่ y เกินขอบนี้เงียบ ๆ\n"
            "  1) รัน make_world_datapack.py เพื่อเขียน datapack\n"
            "  2) รัน make_world_datapack.py --enable (หรือเปิดโลกในเกมหนึ่งครั้ง)\n"
            "  3) ตรวจในเกมด้วย /datapack list ว่าเห็น file/" + PACK_NAME
        )
    print("ขอบเขตถูกต้อง — build_terrain.py เขียนได้เต็มความสูง")


def main():
    if "--verify" in sys.argv:
        verify_world_bounds()
        return
    if "--enable" in sys.argv:
        changed = enable_in_level_dat(C.WORLD_PATH)
        print(
            f"เพิ่ม file/{PACK_NAME} เข้า level.dat แล้ว (สำรองไว้ที่ "
            f"level.dat.before_tall_overworld)" if changed
            else f"file/{PACK_NAME} อยู่ใน Enabled อยู่แล้ว"
        )
        verify_world_bounds()
        return

    errors = validate_config()
    if errors:
        raise SystemExit(
            "config ไม่ผ่านการตรวจ:\n  - " + "\n  - ".join(errors)
        )

    blocks = C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN
    print(
        f"overworld: y {C.WORLD_Y_MIN}..{C.WORLD_Y_MAX} "
        f"({C.WORLD_HEIGHT} บล็อก = {C.WORLD_HEIGHT // 16} sections, "
        f"วานิลลาคือ 384/24)"
    )
    print(
        f"ภูมิประเทศใช้ y {C.Y_TERRAIN_MIN}..{C.Y_TERRAIN_MAX} ({blocks} บล็อก) "
        f"| เหลือหัว {C.Y_BUILD_CEILING - C.Y_TERRAIN_MAX} บล็อก"
    )

    p = paths()
    if "--check" in sys.argv:
        if not os.path.isfile(p["dimension"]):
            raise SystemExit(f"ยังไม่มี datapack ที่ {p['root']}")
        with open(p["dimension"], encoding="utf-8") as f:
            on_disk = json.load(f)
        want = dimension_type()
        diff = {
            k: (on_disk.get(k), want[k])
            for k in ("min_y", "height", "logical_height")
            if on_disk.get(k) != want[k]
        }
        if diff:
            raise SystemExit(f"datapack ไม่ตรงกับ config: {diff}")
        print("datapack ตรงกับ config")
        return

    if not os.path.isdir(C.WORLD_PATH):
        raise SystemExit(f"ไม่พบโลกที่ {C.WORLD_PATH}")
    region_dir = os.path.join(C.WORLD_PATH, "region")
    if os.path.isdir(region_dir) and os.listdir(region_dir):
        print(
            "[เตือน] โลกนี้มีข้อมูล chunk อยู่แล้ว การเปลี่ยนความสูงของ dimension "
            "ทำให้ chunk เดิมอ่านค่าผิดระดับ — ควรใช้โลกใหม่ที่ยังว่าง"
        )

    os.makedirs(os.path.dirname(p["dimension"]), exist_ok=True)
    with open(p["mcmeta"], "w", encoding="utf-8") as f:
        json.dump(pack_mcmeta(), f, ensure_ascii=False, indent=2)
    with open(p["dimension"], "w", encoding="utf-8") as f:
        json.dump(dimension_type(), f, indent=2)
    count = write_biomes(p["root"])
    print(f"เขียน {p['root']}")
    print(f"  dimension_type/overworld.json (ความสูง {C.WORLD_HEIGHT})")
    print(f"  biome ที่ออกแบบเอง {count} ตัว")
    print(
        "\nขั้นถัดไป: เปิดโลกในเกมหนึ่งครั้งเพื่อให้ datapack ถูกโหลด "
        "แล้วออกมา ก่อนรัน build_terrain.py"
    )
    print(
        "ตรวจว่าโหลดสำเร็จ: ในเกมพิมพ์ /datapack list — ต้องเห็น "
        f'"file/{PACK_NAME}" อยู่ในรายการที่เปิดใช้'
    )


if __name__ == "__main__":
    main()

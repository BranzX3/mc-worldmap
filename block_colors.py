"""ดึงสีเฉลี่ยจริงของบล็อกจากไฟล์เกม แล้วจัดเรียงเป็นการไล่สี

ดีกว่าลิสต์สีจากเน็ตเพราะตรงกับเวอร์ชันที่ใช้จริง (26.2) และรวม texture ที่
เพิ่มมาใหม่ทั้งหมด

ใช้:
    python block_colors.py                    # ไล่สีกลุ่มหิน
    python block_colors.py --all              # ทุกบล็อกที่หาได้
    python block_colors.py --grep tuff        # กรองด้วยชื่อ
"""

import io
import json
import os
import sys
import zipfile

import numpy as np
from PIL import Image

JAR = r"C:\Users\User\AppData\Roaming\ModrinthApp\meta\versions\26.2-0.19.3\26.2-0.19.3.jar"
PREFIX = "assets/minecraft/textures/block/"

# เฉพาะบล็อกทึบที่ใช้ปูผิวได้ ไม่เอาพวกโปร่งใส/ไม้/พืช
STONEY = (
    "stone", "cobble", "andesite", "diorite", "granite", "deepslate", "tuff",
    "calcite", "basalt", "blackstone", "dripstone", "gravel", "clay", "mud",
    "terracotta", "concrete", "quartz", "sandstone", "bone", "amethyst",
    "moss", "dirt", "podzol", "grass", "snow", "ice", "packed", "prismarine",
    "sand", "netherrack", "resin", "copper", "iron", "smooth",
)
EXCLUDE = (
    "_side", "_top", "_bottom", "_front", "_back", "_inner", "_outer",
    "_stage", "_overlay", "_still", "_flow", "destroy_", "_on", "_open",
    "door", "trapdoor", "sapling", "_pane", "bud", "cluster", "_powder",
    "_ore", "raw_", "_block_top", "candle", "pot", "_cracked_top",
)


def load():
    out = {}
    with zipfile.ZipFile(JAR) as z:
        names = [
            n for n in z.namelist()
            if n.startswith(PREFIX) and n.endswith(".png") and n.count("/") == 4
        ]
        for n in names:
            base = n[len(PREFIX):-4]
            if any(e in base for e in EXCLUDE):
                continue
            try:
                with z.open(n) as f:
                    img = Image.open(io.BytesIO(f.read())).convert("RGBA")
            except Exception:
                continue
            if img.width != img.height or img.width > 64:
                continue  # ข้าม texture แบบ animation strip
            a = np.asarray(img, dtype=np.float32)
            alpha = a[..., 3] / 255.0
            if alpha.mean() < 0.95:
                continue  # ไม่เอาบล็อกโปร่งใส
            rgb = a[..., :3].reshape(-1, 3)
            mean = rgb.mean(axis=0)
            std = rgb.std(axis=0).mean()
            out[base] = (mean, std)
    return out


def luma(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def sat(c):
    return (c.max() - c.min()) / (c.max() + 1e-6)


def main():
    if not os.path.exists(JAR):
        raise SystemExit(f"ไม่พบไฟล์เกม: {JAR}")
    data = load()
    print(f"อ่าน texture ได้ {len(data)} บล็อก\n")

    if "--all" in sys.argv:
        sel = data
    elif "--grep" in sys.argv:
        q = sys.argv[sys.argv.index("--grep") + 1]
        sel = {k: v for k, v in data.items() if q in k}
    else:
        sel = {
            k: v for k, v in data.items()
            if any(s in k for s in STONEY) and sat(v[0]) < 0.18
        }

    rows = sorted(sel.items(), key=lambda kv: luma(kv[1][0]))
    print(f"{'บล็อก':<32}{'R':>5}{'G':>5}{'B':>5}{'สว่าง':>8}{'ความคละ':>9}")
    print("-" * 72)
    for name, (c, s) in rows:
        print(
            f"{name:<32}{c[0]:5.0f}{c[1]:5.0f}{c[2]:5.0f}{luma(c):8.1f}{s:9.1f}"
        )

    with open("block_colors.json", "w", encoding="utf-8") as f:
        json.dump(
            {k: {"rgb": [round(float(x), 1) for x in v[0]],
                 "luma": round(float(luma(v[0])), 1),
                 "variance": round(float(v[1]), 1)}
             for k, v in sorted(data.items(), key=lambda kv: luma(kv[1][0]))},
            f, indent=1, ensure_ascii=False,
        )
    print(f"\nบันทึกทั้งหมด {len(data)} บล็อกลง block_colors.json")


if __name__ == "__main__":
    main()

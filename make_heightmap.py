"""แปลง DEM เป็น heightmap PNG 16-bit สำหรับ import เข้า Axiom

แหล่งข้อมูลเริ่มต้น: BEV ALS-DGM 1 m ของออสเตรีย (ฟรี, ครอบทั้งประเทศ)
ไฟล์เป็น Cloud-Optimized GeoTIFF ที่มี overview ครบ สคริปต์จึงอ่านผ่าน HTTP
เฉพาะพื้นที่+ระดับความละเอียดที่ต้องการ ไม่ต้องโหลดทั้ง tile (tile ละ ~8 GB)

ใช้:
    python make_heightmap.py              # ดึงจาก BEV ผ่านเน็ต
    python make_heightmap.py dem.tif      # ใช้ไฟล์ในเครื่องแทน

ผลลัพธ์:
    heightmap.png       16-bit grayscale สำหรับ Axiom
    heightmap_info.txt  ค่าที่ต้องกรอกตอน import
    hillshade.png       ภาพเงาไว้ตรวจด้วยตา
"""

import math
import os
import sys

import numpy as np
import rasterio
from rasterio.crs import CRS as RCRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds as window_from_bounds

import config as C
from fetch_map import utm_crs_for

HERE = os.path.dirname(os.path.abspath(__file__))

# BEV ALS DTM 1m — แจกเป็น tile 50x50 km ใน EPSG:3035
BEV_DATE = "20250915"
BEV_URL = (
    "/vsicurl/https://data.bev.gv.at/download/ALS/DTM/{date}/"
    "ALS_DTM_CRS3035RES50000mN{n}E{e}.tif"
)
BEV_CRS = RCRS.from_epsg(3035)  # ไฟล์ประกาศเป็น LOCAL_CS ต้อง override เอง
TILE = 50_000
BUFFER_M = 500  # เผื่อขอบไว้กัน artifact ตอน reproject

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")


def target_grid():
    """กริดปลายทางใน UTM — ต้องตรงกับที่ fetch_map.py ใช้เป๊ะ"""
    import geopandas as gpd

    utm = utm_crs_for(C.CENTER_LAT, C.CENTER_LON)
    center = (
        gpd.GeoSeries.from_xy([C.CENTER_LON], [C.CENTER_LAT], crs="EPSG:4326")
        .to_crs(utm)
        .iloc[0]
    )
    half = C.AREA_SIZE_M / 2
    res = float(C.METERS_PER_BLOCK)
    transform = from_origin(center.x - half, center.y + half, res, res)
    bounds = (center.x - half, center.y - half, center.x + half, center.y + half)
    return RCRS.from_epsg(utm.to_epsg()), transform, bounds, res


def read_mosaic_3035(bounds_3035, res):
    """อ่าน BEV tile ที่จำเป็น ที่ความละเอียด res เมตร แล้วต่อเป็นภาพเดียว"""
    minx, miny, maxx, maxy = bounds_3035
    width = int(math.ceil((maxx - minx) / res))
    height = int(math.ceil((maxy - miny) / res))
    transform = from_origin(minx, maxy, res, res)
    mosaic = np.full((height, width), np.nan, dtype="float32")

    e0 = int(minx // TILE) * TILE
    n0 = int(miny // TILE) * TILE
    tiles = [
        (n, e)
        for e in range(e0, int(maxx) + TILE, TILE)
        for n in range(n0, int(maxy) + TILE, TILE)
    ]
    print(f"ต้องใช้ {len(tiles)} tile: " + ", ".join(f"N{n}E{e}" for n, e in tiles))

    for n, e in tiles:
        url = BEV_URL.format(date=BEV_DATE, n=n, e=e)
        print(f"\n  อ่าน N{n}E{e} ...")
        try:
            src = rasterio.open(url)
        except Exception as exc:
            print(f"    เปิดไม่ได้ ({exc}) — ข้าม")
            continue
        with src:
            sb = src.bounds
            ix = (max(minx, sb.left), max(miny, sb.bottom),
                  min(maxx, sb.right), min(maxy, sb.top))
            if ix[0] >= ix[2] or ix[1] >= ix[3]:
                print("    ไม่ทับกับพื้นที่ — ข้าม")
                continue

            win = window_from_bounds(*ix, transform=src.transform)
            out_w = max(1, int(math.ceil((ix[2] - ix[0]) / res)))
            out_h = max(1, int(math.ceil((ix[3] - ix[1]) / res)))
            print(f"    ดึง {out_w}x{out_h} px (จาก overview — ไม่ใช่ full 50001px)")

            data = src.read(
                1, window=win, out_shape=(out_h, out_w),
                resampling=Resampling.bilinear, boundless=False,
            ).astype("float32")
            if src.nodata is not None:
                data[data == src.nodata] = np.nan

            patch_transform = from_origin(ix[0], ix[3], res, res)
            reproject(
                source=data,
                destination=mosaic,
                src_transform=patch_transform,
                src_crs=BEV_CRS,
                dst_transform=transform,
                dst_crs=BEV_CRS,
                resampling=Resampling.bilinear,
                src_nodata=np.nan,
                dst_nodata=np.nan,
                init_dest_nodata=False,
            )
    return mosaic, transform


def main():
    dst_crs, dst_transform, dst_bounds, res = target_grid()
    shape = (C.GRID, C.GRID)
    dst = np.full(shape, np.nan, dtype="float32")

    local = sys.argv[1] if len(sys.argv) > 1 else None
    if local and not os.path.isabs(local):
        local = os.path.join(HERE, local)

    if local:
        print(f"ใช้ไฟล์ในเครื่อง: {local}")
        with rasterio.open(local) as src:
            print(f"  {src.width}x{src.height}  CRS {src.crs}")
            reproject(
                source=rasterio.band(src, 1),
                destination=dst,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
                src_nodata=src.nodata,
                dst_nodata=np.nan,
            )
    else:
        b = transform_bounds(dst_crs, BEV_CRS, *dst_bounds, densify_pts=64)
        b = (b[0] - BUFFER_M, b[1] - BUFFER_M, b[2] + BUFFER_M, b[3] + BUFFER_M)
        print(f"พื้นที่ใน EPSG:3035  E {b[0]:.0f}..{b[2]:.0f}  N {b[1]:.0f}..{b[3]:.0f}")
        mosaic, mos_transform = read_mosaic_3035(b, res)
        if not np.isfinite(mosaic).any():
            raise SystemExit("อ่าน DEM ไม่ได้เลย — เช็คเน็ตหรือ BEV_DATE")
        print("\nreproject จาก EPSG:3035 -> UTM ...")
        reproject(
            source=mosaic,
            destination=dst,
            src_transform=mos_transform,
            src_crs=BEV_CRS,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
            src_nodata=np.nan,
            dst_nodata=np.nan,
        )

    valid = np.isfinite(dst)
    if not valid.any():
        raise SystemExit("DEM ไม่ครอบคลุมพื้นที่เลย — เช็ค CENTER_LAT/LON")
    print(f"ครอบคลุมพื้นที่ {valid.mean():.2%}")
    if valid.mean() < 0.999:
        print("[เตือน] ไม่ครอบคลุมทั้งกรอบ ช่องที่ขาดจะถูกเติมด้วยค่าต่ำสุด")

    lo, hi = float(np.nanmin(dst)), float(np.nanmax(dst))
    dst = np.where(valid, dst, lo)

    y_lo, y_hi = C.Y_TERRAIN_MIN, C.Y_TERRAIN_MAX
    blocks = y_hi - y_lo
    v_scale = (hi - lo) / blocks

    print(f"\nความสูงจริง {lo:.0f} m ถึง {hi:.0f} m  (ต่าง {hi-lo:.0f} m)")
    print(f"แม็พลง y = {y_lo} ถึง {y_hi}  ({blocks} บล็อก)")
    print(f"แนวตั้ง 1 บล็อก = {v_scale:.2f} m | แนวนอน 1 บล็อก = {res:.0f} m")
    print(f"สัดส่วนเพี้ยน {v_scale/res:.2f} เท่า (1.00 = สมจริงเป๊ะ)")

    from PIL import Image

    png16 = np.clip(np.rint((dst - lo) / (hi - lo) * 65535), 0, 65535).astype(np.uint16)
    out_png = os.path.join(HERE, "heightmap.png")
    Image.fromarray(png16, mode="I;16").save(out_png)
    print(f"\nบันทึก {out_png}  ({C.GRID}x{C.GRID}, 16-bit)")

    info = os.path.join(HERE, "heightmap_info.txt")
    with open(info, "w", encoding="utf-8") as f:
        f.write(
            f"""ค่าที่ต้องกรอกตอน import heightmap ใน Axiom
=============================================
ขนาดภาพ           {C.GRID} x {C.GRID} px
วางที่ (X, Z)      0, 0  ถึง  {C.GRID-1}, {C.GRID-1}
Min Y             {y_lo}
Max Y             {y_hi}

สเกล
  แนวนอน  1 block = {res:.0f} m
  แนวตั้ง  1 block = {v_scale:.2f} m
  เพี้ยน   {v_scale/res:.2f} เท่า

ความสูงจริงที่แม็พ
  {lo:.0f} m -> y = {y_lo}
  {hi:.0f} m -> y = {y_hi}
  แปลงกลับ: ความสูงจริง (m) = {lo:.1f} + (y - ({y_lo})) * {v_scale:.4f}

พื้นที่
  กึ่งกลาง {C.CENTER_LAT}, {C.CENTER_LON}
  ขนาด     {C.AREA_SIZE_M/1000:.0f} x {C.AREA_SIZE_M/1000:.0f} km
  CRS      EPSG:{dst_crs.to_epsg()}

แหล่งข้อมูล
  BEV ALS-DGM 1m (Stichtag {BEV_DATE}) — Bundesamt für Eich- und Vermessungswesen
  ความแม่นยำแนวสูง +-0.5 m
"""
        )
    print(f"บันทึก {info}")

    # meta แบบเครื่องอ่านได้ — surface.py ต้องใช้แปลงค่าพิกเซลกลับเป็นเมตรจริง
    import json

    meta_path = os.path.join(HERE, "heightmap_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "elev_min_m": lo,
                "elev_max_m": hi,
                "y_min": y_lo,
                "y_max": y_hi,
                "meters_per_block_h": res,
                "meters_per_block_v": v_scale,
                "grid": C.GRID,
                "area_size_m": C.AREA_SIZE_M,
                "center": [C.CENTER_LAT, C.CENTER_LON],
                "epsg": dst_crs.to_epsg(),
            },
            f,
            indent=2,
        )
    print(f"บันทึก {meta_path}")

    dy, dx = np.gradient(dst, res)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az, alt = math.radians(315.0), math.radians(45.0)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    out_hs = os.path.join(HERE, "hillshade.png")
    Image.fromarray(np.clip(shade * 255, 0, 255).astype(np.uint8)).resize(
        (1600, 1600), Image.LANCZOS
    ).save(out_hs)
    print(f"บันทึก {out_hs} — เปิดดูก่อนว่าภูเขาอยู่ถูกที่")


if __name__ == "__main__":
    main()

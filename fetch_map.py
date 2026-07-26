"""ขั้นที่ 1: ดึงข้อมูลแม่น้ำ + ป่า จาก OpenStreetMap แล้ว rasterize เป็น mask 10000x10000

ผลลัพธ์: masks.npz  (river, forest -> bool array shape (GRID, GRID))
แถวที่ 0 = เหนือสุด, คอลัมน์ 0 = ตะวันตกสุด
"""

import numpy as np
import geopandas as gpd
import osmnx as ox
from pyproj import CRS
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import box

import config as C


def utm_crs_for(lat: float, lon: float) -> CRS:
    """หา UTM zone ที่ตรงกับจุดนี้ เพื่อให้หน่วยเป็นเมตรจริง (ไม่บิดเบี้ยว)"""
    zone = int((lon + 180) / 6) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def fetch(tags: dict, bbox_wgs84) -> gpd.GeoDataFrame:
    """ดึง features จาก Overpass ตาม tag; คืน GeoDataFrame ว่างถ้าไม่เจอ"""
    try:
        gdf = ox.features_from_polygon(bbox_wgs84, tags)
    except ox._errors.InsufficientResponseError:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gdf[gdf.geometry.notna()]


def main():
    utm = utm_crs_for(C.CENTER_LAT, C.CENTER_LON)
    half = C.AREA_SIZE_M / 2

    # กล่องพื้นที่ในระบบเมตร แล้วแปลงกลับเป็น lat/lon ไว้ query Overpass
    center_utm = (
        gpd.GeoSeries.from_xy([C.CENTER_LON], [C.CENTER_LAT], crs="EPSG:4326")
        .to_crs(utm)
        .iloc[0]
    )
    minx, miny = center_utm.x - half, center_utm.y - half
    maxx, maxy = center_utm.x + half, center_utm.y + half

    bbox_utm = box(minx, miny, maxx, maxy)
    bbox_wgs84 = (
        gpd.GeoSeries([bbox_utm], crs=utm).to_crs("EPSG:4326").iloc[0]
    )

    print(f"UTM: {utm.to_epsg()}  bbox: {minx:.0f},{miny:.0f} -> {maxx:.0f},{maxy:.0f}")

    # --- ป่า ---
    print("ดึงข้อมูลป่า...")
    forest = fetch(
        {"landuse": ["forest"], "natural": ["wood", "scrub"]}, bbox_wgs84
    )
    forest_geoms = []
    if len(forest):
        f = forest.to_crs(utm)
        forest_geoms = [g for g in f.geometry if g.geom_type in ("Polygon", "MultiPolygon")]
    print(f"  {len(forest_geoms)} polygons")

    # --- น้ำ: polygon (แม่น้ำใหญ่/ทะเลสาบ) + line (ลำธาร ต้อง buffer) ---
    print("ดึงข้อมูลแหล่งน้ำ...")
    water_poly = fetch({"natural": ["water"], "landuse": ["reservoir", "basin"]}, bbox_wgs84)
    waterway = fetch({"waterway": list(C.RIVER_WIDTH_M.keys()) + ["riverbank"]}, bbox_wgs84)

    river_geoms = []
    if len(water_poly):
        w = water_poly.to_crs(utm)
        river_geoms += [g for g in w.geometry if g.geom_type in ("Polygon", "MultiPolygon")]
    if len(waterway):
        w = waterway.to_crs(utm)
        for geom, kind in zip(w.geometry, w.get("waterway", [None] * len(w))):
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                river_geoms.append(geom)
            elif geom.geom_type in ("LineString", "MultiLineString"):
                width = C.RIVER_WIDTH_M.get(kind, C.DEFAULT_RIVER_WIDTH_M)
                river_geoms.append(geom.buffer(width / 2, cap_style=2))
    print(f"  {len(river_geoms)} geometries")

    # --- rasterize ลง grid ---
    # from_origin(west, north, xsize, ysize): แถว 0 อยู่เหนือสุด
    res = float(C.METERS_PER_BLOCK)
    transform = from_origin(minx, maxy, res, res)
    shape = (C.GRID, C.GRID)

    def burn(geoms):
        if not geoms:
            return np.zeros(shape, dtype=bool)
        return rasterize(
            ((g, 1) for g in geoms),
            out_shape=shape,
            transform=transform,
            fill=0,
            all_touched=True,   # ลำธารบางๆ จะไม่หายไป
            dtype="uint8",
        ).astype(bool)

    print("rasterize ป่า...")
    forest_mask = burn(forest_geoms)
    print("rasterize แม่น้ำ...")
    river_mask = burn(river_geoms)

    if C.RIVER_OVER_FOREST:
        forest_mask &= ~river_mask

    print(
        f"ป่า {forest_mask.sum():,} blocks ({forest_mask.mean():.1%}) | "
        f"แม่น้ำ {river_mask.sum():,} blocks ({river_mask.mean():.1%})"
    )

    np.savez_compressed(
        C.MASK_FILE,
        river=river_mask,
        forest=forest_mask,
        origin_utm=np.array([minx, maxy]),
        epsg=utm.to_epsg(),
    )
    print(f"บันทึก {C.MASK_FILE}")

    # preview PNG ไว้ตรวจก่อนเขียนโลกจริง
    try:
        from PIL import Image

        img = np.full((*shape, 3), 32, dtype=np.uint8)
        img[forest_mask] = (60, 160, 60)
        img[river_mask] = (60, 110, 220)
        Image.fromarray(img).resize((2000, 2000), Image.NEAREST).save("preview.png")
        print("บันทึก preview.png")
    except ImportError:
        pass


if __name__ == "__main__":
    main()

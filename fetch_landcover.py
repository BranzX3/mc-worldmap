"""ดึง landcover หลายชั้นจาก OSM แล้ว rasterize เป็น class raster ชั้นเดียว

ต่างจาก fetch_map.py ที่ดึงแค่ป่ากับน้ำ — ตัวนี้ดึงทุกประเภทพื้นผิวที่มีผลต่อสีในเกม
เก็บเป็น uint8 array เดียว (ไม่ใช่ mask แยกชั้น) เพื่อไม่ให้กิน RAM บานปลายที่ 10000x10000

ผลลัพธ์: landcover.npz  (class array + ตารางรหัส)
"""

import numpy as np
import geopandas as gpd
import osmnx as ox
import sys
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import box

import config as C
from fetch_map import utm_crs_for

# เรียงจากความสำคัญน้อยไปมาก — ตัวหลังทับตัวหน้า
# น้ำอยู่บนสุดเพราะแม่น้ำต้องชนะทุกอย่างที่มันไหลผ่าน
CLASSES = [
    ("grass", 1, {"natural": ["grassland", "fell"], "landuse": ["meadow", "grass", "village_green"]}),
    ("farm", 2, {"landuse": ["farmland", "orchard", "vineyard", "allotments"]}),
    ("forest", 3, {"landuse": ["forest"], "natural": ["wood"]}),
    ("scrub", 4, {"natural": ["scrub", "heath"]}),
    ("wetland", 5, {"natural": ["wetland"], "landuse": ["basin"]}),
    ("scree", 6, {"natural": ["scree", "shingle"]}),
    ("rock", 7, {"natural": ["bare_rock", "cliff"]}),
    ("glacier", 8, {"natural": ["glacier"]}),
    ("water", 9, {"natural": ["water"], "landuse": ["reservoir"]}),
]

WATERWAY_WIDTH_M = C.RIVER_WIDTH_M
CODE = {name: code for name, code, _ in CLASSES}
WATERWAY_KIND_CODE = {
    "river": 1,
    "canal": 2,
    "stream": 3,
    "ditch": 4,
    "drain": 5,
    "riverbank": 6,
}


def ordered_line_parts(geometry):
    """Yield LineStrings without reversing their OSM node order."""
    if geometry.geom_type == "LineString":
        yield geometry
    elif geometry.geom_type == "MultiLineString":
        yield from geometry.geoms


def pack_ordered_waterways(records, minx, maxy, resolution):
    """Pack directed UTM line records into compact world-coordinate arrays."""
    points_x = []
    points_z = []
    offsets = [0]
    kinds = []
    widths = []
    for geometry, kind, width_m in records:
        for line in ordered_line_parts(geometry):
            coords = np.asarray(line.coords, dtype=np.float64)
            if len(coords) < 2:
                continue
            points_x.extend((coords[:, 0] - minx) / resolution)
            points_z.extend((maxy - coords[:, 1]) / resolution)
            offsets.append(len(points_x))
            kinds.append(WATERWAY_KIND_CODE.get(kind, 0))
            widths.append(float(width_m))
    return {
        "points_x": np.asarray(points_x, dtype=np.float32),
        "points_z": np.asarray(points_z, dtype=np.float32),
        "offsets": np.asarray(offsets, dtype=np.int32),
        "kind": np.asarray(kinds, dtype=np.uint8),
        "width_m": np.asarray(widths, dtype=np.float32),
    }


def fetch(tags, poly):
    try:
        gdf = ox.features_from_polygon(poly, tags)
    except Exception as exc:
        print(f"    (ไม่พบ / {type(exc).__name__})")
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gdf[gdf.geometry.notna()]


def main():
    utm = utm_crs_for(C.CENTER_LAT, C.CENTER_LON)
    half = C.AREA_SIZE_M / 2
    center = (
        gpd.GeoSeries.from_xy([C.CENTER_LON], [C.CENTER_LAT], crs="EPSG:4326")
        .to_crs(utm)
        .iloc[0]
    )
    minx, miny = center.x - half, center.y - half
    maxx, maxy = center.x + half, center.y + half
    poly = gpd.GeoSeries([box(minx, miny, maxx, maxy)], crs=utm).to_crs("EPSG:4326").iloc[0]

    res = float(C.METERS_PER_BLOCK)
    transform = from_origin(minx, maxy, res, res)
    shape = (C.GRID, C.GRID)
    water_only = "--water-only" in sys.argv
    if water_only:
        existing = np.load("landcover.npz")["landcover"]
        if existing.shape != shape:
            raise SystemExit("landcover.npz shape ไม่ตรงกับ GRID ปัจจุบัน")
        out = existing.copy()
        classes_to_fetch = [
            item for item in CLASSES if item[0] == "water"
        ]
        print("[MODE] water-only: เก็บ source แยกโดยไม่เขียนทับ landcover.npz")
    else:
        out = np.zeros(shape, dtype=np.uint8)
        classes_to_fetch = CLASSES
    waterbody_mask = np.zeros(shape, dtype=bool)

    print(f"พื้นที่ {C.AREA_SIZE_M/1000:.0f}x{C.AREA_SIZE_M/1000:.0f} km -> {C.GRID}x{C.GRID} @ {res:.0f} m/block\n")

    for name, code, tags in classes_to_fetch:
        print(f"[{code}] {name} ...")
        gdf = fetch(tags, poly)
        geoms = []
        if len(gdf):
            g = gdf.to_crs(utm)
            geoms = [x for x in g.geometry if x.geom_type in ("Polygon", "MultiPolygon")]
        print(f"    {len(geoms)} polygons")
        if geoms:
            burn = rasterize(
                ((x, code) for x in geoms),
                out_shape=shape,
                transform=transform,
                fill=0,
                all_touched=True,
                dtype="uint8",
            )
            if not water_only:
                out[burn > 0] = code
            if name == "water":
                waterbody_mask |= burn > 0
        del geoms, gdf

    # ลำธาร/แม่น้ำที่เป็นเส้น ต้อง buffer ตามชนิด แล้วทับท้ายสุด
    print("[9] waterway (เส้น -> buffer) ...")
    ways = fetch({"waterway": list(WATERWAY_WIDTH_M.keys()) + ["riverbank"]}, poly)
    line_geoms = []
    directed_records = []
    kind_geoms = []
    if len(ways):
        w = ways.to_crs(utm)
        kinds = w["waterway"] if "waterway" in w.columns else [None] * len(w)
        for geom, kind in zip(w.geometry, kinds):
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                line_geoms.append(geom)
                kind_geoms.append((geom, WATERWAY_KIND_CODE["riverbank"]))
            elif geom.geom_type in ("LineString", "MultiLineString"):
                width = WATERWAY_WIDTH_M.get(kind, C.DEFAULT_RIVER_WIDTH_M)
                buffered = geom.buffer(width / 2, cap_style=2)
                line_geoms.append(buffered)
                kind_geoms.append((
                    buffered, WATERWAY_KIND_CODE.get(kind, 0)
                ))
                directed_records.append((geom, kind, width))
    print(f"    {len(line_geoms)} geometries")
    if line_geoms:
        burn = rasterize(
            ((x, CODE["water"]) for x in line_geoms),
            out_shape=shape,
            transform=transform,
            fill=0,
            all_touched=True,
            dtype="uint8",
        )
        if not water_only:
            out[burn > 0] = CODE["water"]
        waterway_mask = burn > 0
        waterway_kind = rasterize(
            kind_geoms,
            out_shape=shape,
            transform=transform,
            fill=0,
            all_touched=True,
            dtype="uint8",
        )
    else:
        waterway_mask = np.zeros(shape, dtype=bool)
        waterway_kind = np.zeros(shape, dtype=np.uint8)

    print("\nสัดส่วนแต่ละชั้น:")
    total = out.size
    print(f"  {'(ไม่ระบุ)':<12} {(out==0).sum()/total:6.2%}")
    for name, code, _ in CLASSES:
        print(f"  {name:<12} {(out==code).sum()/total:6.2%}")

    if not water_only:
        np.savez_compressed(
            "landcover.npz",
            landcover=out,
            names=np.array([n for n, _, _ in CLASSES]),
            codes=np.array([c for _, c, _ in CLASSES]),
        )
    packed = pack_ordered_waterways(
        directed_records, minx=minx, maxy=maxy, resolution=res
    )
    np.savez_compressed(
        "water_sources.npz",
        waterbody_mask=waterbody_mask,
        waterway_mask=waterway_mask,
        waterway_kind=waterway_kind,
        **packed,
    )
    print(
        "\nบันทึก "
        + ("water_sources.npz " if water_only
           else "landcover.npz + water_sources.npz ")
        + f"({len(packed['kind']):,} directed lines)"
    )


if __name__ == "__main__":
    main()

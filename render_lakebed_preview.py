"""Render the lakebed substrate plan without changing the Minecraft world.

Usage:
    python render_lakebed_preview.py
    python render_lakebed_preview.py 1400 4942 5610 1100
"""

import os
import sys

import numpy as np
from PIL import Image

import config as C
import paint_surface as P
import surface as S

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    out_px = int(sys.argv[1]) if len(sys.argv) > 1 else 1400
    cx = int(sys.argv[2]) if len(sys.argv) > 2 else 4942
    cz = int(sys.argv[3]) if len(sys.argv) > 3 else 5610
    radius = int(sys.argv[4]) if len(sys.argv) > 4 else 1100

    x0, x1 = max(0, cx - radius), min(C.GRID, cx + radius)
    z0, z1 = max(0, cz - radius), min(C.GRID, cz + radius)
    pad = P.PAD
    ax0, ax1 = max(0, x0 - pad), min(C.GRID, x1 + pad)
    az0, az1 = max(0, z0 - pad), min(C.GRID, z1 + pad)

    heightmap = np.asarray(Image.open(os.path.join(HERE, "heightmap.png")))
    raw = heightmap[az0:az1, ax0:ax1].T.astype(np.float32)
    surf_y = np.rint(
        C.Y_TERRAIN_MIN
        + raw / 65535.0 * (C.Y_TERRAIN_MAX - C.Y_TERRAIN_MIN)
    ).astype(np.int32)
    water = np.load(
        os.path.join(HERE, "water_mask.npy"), mmap_mode="r"
    )[az0:az1, ax0:ax1].T
    depth = np.load(
        os.path.join(HERE, "water_depth.npy"), mmap_mode="r"
    )[az0:az1, ax0:ax1].T.astype(np.int32)

    adjusted, lake = P.lake_surface_levels(surf_y, water, depth)
    sx = slice(x0 - ax0, x1 - ax0)
    sz = slice(z0 - az0, z1 - az0)
    depth = depth[sx, sz]
    water = water[sx, sz]
    lake = lake[sx, sz]
    bed_y = adjusted[sx, sz] - depth
    relief = P.neighbour_relief(bed_y)
    kind = P.lakebed_materials(depth, relief, lake, x0=x0, z0=z0)

    block_key = {
        "stream": "moss",
        "sand": "sand" if "sand" in S.BLOCKS else "rooted",
        "gravel": "gravel",
        "clay": "clay",
        "mud": "mud",
        "stone": "stone",
        "cobble": "cobble",
        "calcite": "calcite",
    }
    # Sand is not part of the surface preview palette, so use its vanilla-like
    # top color directly.
    colors = []
    for name in P.LAKEBED_NAMES:
        if name == "sand":
            colors.append((219, 207, 163))
        else:
            colors.append(S.BLOCKS[block_key[name]][1])
    palette = np.asarray(colors, dtype=np.float32)
    rgb = palette[kind]

    # Deep substrate is less visible from above; a gentle depth shade makes
    # the bathymetric structure readable without hiding the material palette.
    shade = 1.0 - 0.26 * np.clip(depth.astype(np.float32) / 30.0, 0.0, 1.0)
    rgb *= shade[..., None]
    short, tall, lily = P.aquatic_vegetation_masks(
        depth, kind, relief, x0=x0, z0=z0
    )
    rgb[short] = (66, 116, 52)
    rgb[tall] = (45, 139, 56)
    rgb[lily] = (83, 139, 55)
    rgb[~water] = (22, 27, 24)

    rgb = np.clip(rgb, 0, 255).astype(np.uint8).transpose(1, 0, 2)
    h, w = rgb.shape[:2]
    scale = min(1.0, out_px / max(h, w))
    if scale < 1.0:
        rgb = np.asarray(Image.fromarray(rgb).resize(
            (max(1, round(w * scale)), max(1, round(h * scale))),
            Image.Resampling.NEAREST,
        ))

    out = os.path.join(
        HERE, f"lakebed_preview_x{cx}_z{cz}_r{radius}.png"
    )
    Image.fromarray(rgb).save(out)

    lake_kinds = kind[water]
    print(f"saved {out}")
    print("lakebed material share:")
    for code, name in enumerate(P.LAKEBED_NAMES):
        count = int((lake_kinds == code).sum())
        if count:
            print(f"  {name:<8} {count / lake_kinds.size:6.2%}")
    print(
        "aquatic vegetation: "
        f"short {int(short.sum()):,}, tall {int(tall.sum()):,}, "
        f"lily {int(lily.sum()):,}; deepest "
        f"{int(depth[short | tall].max(initial=0))} blocks"
    )


if __name__ == "__main__":
    main()

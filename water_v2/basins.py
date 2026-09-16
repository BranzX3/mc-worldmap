"""Conservative lake evidence from a complete level core and its openings."""
import numpy as np
from scipy import ndimage as ndi

CARDINAL = ndi.generate_binary_structure(2, 1)


def standing_basins(base, body, settings):
    """Return lake IDs and observed levels, without carving or bank repairs.

    A quantized contour strip on a river can be both broad and locally flat.
    Lake evidence additionally requires each inlet/outlet to be narrower than
    half the core diameter. A river that stays equally wide through a contour
    strip therefore keeps its gradient and ordinary channel depth.
    """
    lakes = np.zeros(base.shape, dtype=np.int32)
    levels, candidates = [], []
    for level in np.unique(base[body]):
        labels, _ = ndi.label(body & (base == level), CARDINAL)
        for ident, region in enumerate(ndi.find_objects(labels), start=1):
            if region is None:
                continue
            core = labels[region] == ident
            area = int(core.sum())
            if area < settings.lake_min_cells:
                continue
            radius = float(ndi.distance_transform_edt(np.pad(core, 1)).max())
            if radius >= settings.lake_min_radius:
                candidates.append((area, int(level), radius, region, core))
    boundary = np.zeros(body.shape, dtype=bool)
    boundary[[0,-1],:] = True
    boundary[:,[0,-1]] = True
    for _, level, radius, region, core in sorted(candidates, key=lambda c: (-c[0], c[1], c[3][0].start, c[3][1].start)):
        seed = np.zeros(body.shape, dtype=bool)
        seed[region] = core & (lakes[region] == 0)
        if not seed.any():
            continue
        grown = ndi.binary_propagation(seed, structure=CARDINAL,
                    mask=body & (np.abs(base-level) <= 1) & (lakes == 0))
        shore = ndi.binary_dilation(grown, structure=CARDINAL) & ~body
        if shore.any() and int(base[shore].min()) < level-1:
            continue
        # Include crop openings: a river crossing the scene does not become
        # a closed basin merely because its outlet was clipped off the map.
        opening = grown & (ndi.binary_dilation(body & ~grown, structure=CARDINAL) | boundary)
        mouths, _ = ndi.label(opening, np.ones((3,3), dtype=bool))
        wide_opening = False
        for mouth in ndi.find_objects(mouths):
            if mouth is not None and max(axis.stop-axis.start for axis in mouth) > radius:
                wide_opening = True
                break
        if wide_opening:
            continue
        levels.append(min(level, int(base[shore].min())) if shore.any() else level)
        lakes[grown] = len(levels)
    return lakes, levels

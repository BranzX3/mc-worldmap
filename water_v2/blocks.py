"""Translate an explicit water product into vanilla blocks without reshaping it.

Products use image indexing ``[z, x]``. ``terrain_y`` is the solid bed in a
wet column and the ground height in a dry column. This module consumes final
material, plant and fluid-state decisions; it does not choose any of them.
The emitted interval starts at the bed (or a requested dry bank top). Filling
the terrain below that interval belongs to the general terrain writer.
"""

from collections.abc import Mapping

import numpy as np


UNRESOLVED = np.iinfo(np.int16).min
MATERIAL_NAMES = (
    "minecraft:stone",
    "minecraft:gravel",
    "minecraft:sand",
    "minecraft:clay",
    "minecraft:mud",
    "minecraft:cobblestone",
)

_FIELD_DTYPES = {
    "terrain_y": np.dtype(np.int16),
    "water_mask": np.dtype(np.bool_),
    "standing_water_mask": np.dtype(np.bool_),
    "surface_y": np.dtype(np.int16),
    "depth": np.dtype(np.uint8),
    "water_level": np.dtype(np.uint8),
    "waterfall_top_y": np.dtype(np.int16),
    "bed_material": np.dtype(np.uint8),
    "aquatic_plant": np.dtype(np.uint8),
    "bank_mask": np.dtype(np.bool_),
    "bank_material": np.dtype(np.uint8),
}


def _column_values(product, local_x, local_z):
    if not isinstance(product, Mapping):
        raise TypeError("water product must be a mapping of arrays")
    if any(
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, (int, np.integer))
        for value in (local_x, local_z)
    ):
        raise TypeError("column coordinates must be integer local x and z")
    arrays = {}
    for name, dtype in _FIELD_DTYPES.items():
        if name not in product:
            raise ValueError(f"water product is missing {name}")
        field = np.asarray(product[name])
        if field.ndim != 2:
            raise ValueError(f"{name} must be a two-dimensional [z, x] array")
        if field.dtype != dtype:
            raise ValueError(f"{name} must have dtype {dtype}, got {field.dtype}")
        arrays[name] = field
    shape = arrays["terrain_y"].shape
    if any(field.shape != shape for field in arrays.values()):
        raise ValueError("all water product fields must have the same shape")
    if not (0 <= local_x < shape[1] and 0 <= local_z < shape[0]):
        raise IndexError(f"column ({local_x}, {local_z}) is outside shape {shape}")
    return {
        name: field[local_z, local_x].item()
        for name, field in arrays.items()
    }


def _material(code, field):
    if not 0 <= code < len(MATERIAL_NAMES):
        raise ValueError(f"{field} has unknown material code {code}")
    return MATERIAL_NAMES[code]


def iter_column_blocks(product, local_x, local_z):
    """Yield final ``(y, namespaced_name, vanilla_properties)`` in ascending Y.

    A flowing column has falling water below its explicitly supplied surface
    state. A standing column has source water below its source surface. A
    curtain adds only level-8 falling blocks above the ordinary surface.

    Plants are emitted only when explicitly requested. Submerged plants need
    source water at their occupied positions and leave the surface water block
    intact. A lily pad needs a source surface and no curtain above it. All
    validation is completed before yielding any block, so a caller cannot
    partially write a malformed column.
    """
    c = _column_values(product, local_x, local_z)
    ground = c["terrain_y"]
    stage = c["surface_y"]
    depth = c["depth"]
    level = c["water_level"]
    curtain = c["waterfall_top_y"]
    plant = c["aquatic_plant"]
    if ground == UNRESOLVED:
        raise ValueError("terrain_y cannot be unresolved")
    if not 0 <= plant <= 3:
        raise ValueError(f"aquatic_plant has unknown plant code {plant}")
    if not c["water_mask"]:
        if c["standing_water_mask"]:
            raise ValueError("a dry column cannot be standing water")
        if depth != 0 or stage != UNRESOLVED or curtain != UNRESOLVED:
            raise ValueError("a dry column must have zero depth and unresolved water heights")
        if plant:
            raise ValueError("aquatic plants require a wet column")
        if c["bank_mask"]:
            yield ground, _material(c["bank_material"], "bank_material"), {}
        return

    if c["bank_mask"]:
        raise ValueError("a wet column cannot also be a dry bank")
    if stage == UNRESOLVED or depth < 1 or stage - ground != depth:
        raise ValueError("wet depth must equal surface_y minus terrain_y and be positive")
    if not 0 <= level <= 8:
        raise ValueError(f"water_level must be a vanilla fluid level 0..8, got {level}")
    if c["standing_water_mask"] and level != 0:
        raise ValueError("standing water requires a source surface (water_level 0)")
    if curtain != UNRESOLVED and curtain <= stage:
        raise ValueError("waterfall_top_y must be above surface_y or unresolved")

    bed = _material(c["bed_material"], "bed_material")
    below_level = 0 if c["standing_water_mask"] else 8
    blocks = {ground: (bed, {})}
    for y in range(ground + 1, stage + 1):
        fluid_level = level if y == stage else below_level
        blocks[y] = ("minecraft:water", {"level": str(fluid_level)})
    if curtain != UNRESOLVED:
        for y in range(stage + 1, curtain + 1):
            blocks[y] = ("minecraft:water", {"level": "8"})

    if plant in (1, 2):
        height = 1 if plant == 1 else 2
        plant_ys = range(ground + 1, ground + height + 1)
        if ground + height >= stage:
            raise ValueError("submerged plants must fit below the surface water block")
        if any(blocks[y] != ("minecraft:water", {"level": "0"}) for y in plant_ys):
            raise ValueError("submerged plants can replace only source water")
        if plant == 1:
            blocks[ground + 1] = ("minecraft:seagrass", {})
        else:
            blocks[ground + 1] = ("minecraft:tall_seagrass", {"half": "lower"})
            blocks[ground + 2] = ("minecraft:tall_seagrass", {"half": "upper"})
    elif plant == 3:
        if level != 0 or curtain != UNRESOLVED:
            raise ValueError("a lily pad requires a source surface without a curtain")
        blocks[stage + 1] = ("minecraft:lily_pad", {})

    for y in sorted(blocks):
        name, properties = blocks[y]
        yield y, name, properties


def _canonical_block(name, properties):
    if not isinstance(name, str) or not name:
        raise ValueError("block name must be a nonempty string")
    if not isinstance(properties, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in properties.items()
    ):
        raise ValueError("block properties must map strings to strings")
    return {
        "name": name if ":" in name else f"minecraft:{name}",
        "properties": dict(properties),
    }


def compare_column_blocks(expected, observed):
    """Return concrete differences for a bounded, explicitly observed interval.

    ``expected`` is an iterable of writer tuples. ``observed`` maps Y to
    ``(name, properties)``. Observe only the product's emitted interval: extra
    keys are reported as unexpected blocks, as are missing keys. Property
    equality is exact, including the water level; flowing/falling water is
    accepted when planned and rejected when replaced with source water.
    """
    if not isinstance(observed, Mapping):
        raise TypeError("observed blocks must map Y to (name, properties)")
    planned = {}
    for y, name, properties in expected:
        if isinstance(y, (bool, np.bool_)) or not isinstance(y, (int, np.integer)):
            raise ValueError("expected block Y must be an integer")
        if y in planned:
            raise ValueError(f"expected contains duplicate Y {y}")
        planned[int(y)] = _canonical_block(name, properties)
    actual = {}
    for y, (name, properties) in observed.items():
        if isinstance(y, (bool, np.bool_)) or not isinstance(y, (int, np.integer)):
            raise ValueError("observed block Y must be an integer")
        actual[int(y)] = _canonical_block(name, properties)
    mismatches = []
    for y in sorted(planned.keys() | actual.keys()):
        want, got = planned.get(y), actual.get(y)
        if want == got:
            continue
        reason = (
            "unexpected" if want is None
            else "missing" if got is None
            else "block" if want["name"] != got["name"]
            else "properties"
        )
        mismatches.append({"y": y, "reason": reason, "expected": want, "observed": got})
    return mismatches

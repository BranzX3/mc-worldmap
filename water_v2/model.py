"""The water product is the contract; writers do not reinterpret it."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

from . import ENGINE, SCHEMA_VERSION
from .diagnostics import FLAGS, validate_report

DRY = np.iinfo(np.int16).min


@dataclass(frozen=True)
class Settings:
    river_incision: int = 1
    max_excavation: int = 4
    max_lake_depth: int = 31
    lake_shelf: float = 26.0
    max_river_depth: int = 4
    ordinary_drop: int = 2
    max_stage_raise: int = 1
    lake_min_cells: int = 48
    lake_min_radius: float = 3.0
    min_y: int = -64
    max_y: int = 718

    def __post_init__(self):
        integer_fields = ("river_incision", "max_excavation", "max_lake_depth",
                          "max_river_depth", "ordinary_drop", "max_stage_raise",
                          "lake_min_cells", "min_y", "max_y")
        for key in integer_fields:
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise ValueError(f"{key} must be an integer")
        if self.river_incision < 0 or self.max_stage_raise < 0 or self.max_excavation < 1:
            raise ValueError("terrain modification limits must be nonnegative")
        if not (1 <= self.max_river_depth <= 255 and 1 <= self.max_lake_depth <= 255):
            raise ValueError("water depths must fit positive uint8")
        if self.ordinary_drop < 1 or self.lake_min_cells < 1:
            raise ValueError("drop and lake size limits must be positive")
        if not all(np.isfinite(v) and v > 0 for v in (self.lake_shelf, self.lake_min_radius)):
            raise ValueError("lake dimensions must be finite and positive")
        if not DRY < self.min_y < self.max_y < np.iinfo(np.int16).max:
            raise ValueError("dimension heights must fit resolved int16")


FIELDS = {
    "terrain_y": np.dtype("int16"), "water_mask": np.dtype("bool"),
    "standing_water_mask": np.dtype("bool"), "surface_y": np.dtype("int16"),
    "depth": np.dtype("uint8"), "water_level": np.dtype("uint8"),
    "waterfall_top_y": np.dtype("int16"), "bed_material": np.dtype("uint8"),
    "bank_mask": np.dtype("bool"), "bank_material": np.dtype("uint8"),
    "aquatic_plant": np.dtype("uint8"), "section_id": np.dtype("int32"),
    "receiver": np.dtype("int32"), "flow_index": np.dtype("uint8"),
    "river_pool_mask": np.dtype("bool"), "diagnostic_flags": np.dtype("uint16"),
}


def _validate_sections(arrays):
    """A shared section is one connected water surface, not just a label."""
    wet = arrays["water_mask"]
    section = arrays["section_id"]
    if np.any(section[wet] <= 0) or np.any(section[~wet] != 0):
        raise ValueError("section IDs must be positive on water and zero on dry cells")
    if not wet.any():
        return
    identifiers, group = np.unique(section[wet], return_inverse=True)
    stages = arrays["surface_y"][wet]
    low = np.full(len(identifiers), np.iinfo(np.int16).max, dtype=np.int16)
    high = np.full(len(identifiers), DRY, dtype=np.int16)
    np.minimum.at(low, group, stages)
    np.maximum.at(high, group, stages)
    if np.any(low != high):
        raise ValueError("every section must have one surface elevation")

    # Compact to wet columns so sparse/nonconsecutive section IDs cannot
    # request huge allocations. Cardinal edges never cross another section.
    column = np.full(wet.shape, -1, dtype=np.int32)
    column[wet] = np.arange(int(wet.sum()), dtype=np.int32)
    starts, ends = [], []
    for a, b in ((np.s_[1:, :], np.s_[:-1, :]),
                 (np.s_[:, 1:], np.s_[:, :-1])):
        shared = wet[a] & wet[b] & (section[a] == section[b])
        starts.append(column[a][shared])
        ends.append(column[b][shared])
    starts, ends = np.concatenate(starts), np.concatenate(ends)
    graph = csr_matrix((np.ones(len(starts), dtype=bool), (starts, ends)),
                       shape=(len(group), len(group)))
    count = connected_components(graph, directed=False, return_labels=False)
    if count != len(identifiers):
        raise ValueError("each section ID must have one cardinally connected footprint")


def _validate_receiver_paths(wet, receiver):
    """Receivers may span a section, but cannot jump water bodies or cycle."""
    active = np.flatnonzero(receiver >= 0)
    if not len(active):
        return
    components, _ = ndi.label(wet, ndi.generate_binary_structure(2, 1))
    component = components.ravel()
    if np.any(component[active] != component[receiver[active]]):
        raise ValueError("receivers cannot cross disconnected wet components")
    # Each column has at most one receiver. Visit each path only once and
    # avoid recursion so long rivers cannot exceed Python's recursion limit.
    state = np.zeros(wet.size, dtype=np.uint8)
    for start in active:
        if state[start]:
            continue
        path = []
        at = int(start)
        while at >= 0 and state[at] == 0:
            state[at] = 1
            path.append(at)
            at = int(receiver[at])
        if at >= 0 and state[at] == 1:
            raise ValueError("receiver paths cannot contain a cycle")
        state[path] = 2


def validate(product):
    """Validate representation separately from physical/visual acceptance."""
    bounds = np.asarray(product["bounds"])
    if np.ma.isMaskedArray(product["bounds"]) and np.ma.getmaskarray(product["bounds"]).any():
        raise ValueError("bounds cannot contain masked values")
    if bounds.shape != (4,) or bounds.dtype.kind not in "iu":
        raise ValueError("bounds must be four integers")
    x0, x1, z0, z1 = map(int, bounds)
    if x0 < 0 or z0 < 0 or x1 <= x0 or z1 <= z0:
        raise ValueError("bounds must be a positive world rectangle")
    shape = (z1-z0, x1-x0)
    if max(x1, z1) > np.iinfo(np.int32).max or shape[0]*shape[1] > np.iinfo(np.int32).max:
        raise ValueError("bounds and local receiver indices must fit int32")
    arrays = {}
    for key, dtype in FIELDS.items():
        value = np.asarray(product[key])
        if np.ma.isMaskedArray(product[key]) and np.ma.getmaskarray(product[key]).any():
            raise ValueError(f"{key} cannot contain masked values")
        if value.shape != shape or value.dtype != dtype:
            raise ValueError(f"{key} must have shape {shape}, dtype {dtype}")
        arrays[key] = value
    # Use the checked arrays consistently, including array-like boolean
    # fields; otherwise a valid nested list fails later at a bitwise operator.
    product = arrays
    wet = product["water_mask"]
    standing = product["standing_water_mask"]
    if np.any(standing & ~wet) or np.any(product["bank_mask"] & wet):
        raise ValueError("standing/bank classification is inconsistent")
    if np.any(product['river_pool_mask'] & ~standing):
        raise ValueError('river pools must be standing source volumes')
    if np.any(product['diagnostic_flags'] & np.uint16(65535-sum(FLAGS.values()))):
        raise ValueError('unknown diagnostic flags')
    if np.any(product["surface_y"][~wet] != DRY) or np.any(product["depth"][~wet]):
        raise ValueError("dry cells must have no stage/depth")
    if np.any(product["depth"][wet] == 0):
        raise ValueError("wet cells must contain a positive water column")
    if np.any(product["terrain_y"] == DRY) or np.any(product["surface_y"][wet] == DRY):
        raise ValueError("terrain and wet surface elevations cannot be unresolved")
    if np.any(product["terrain_y"][wet].astype(int) !=
              product["surface_y"][wet].astype(int)-product["depth"][wet]):
        raise ValueError("terrain bed must agree with stage and depth")
    if np.any(product["water_level"] > 8):
        raise ValueError("fluid state must be source, horizontal flow, or falling")
    if np.any(product["water_level"][standing]):
        raise ValueError("standing water must have source surfaces")
    receiver = product["receiver"].ravel()
    active = receiver >= 0
    if np.any(receiver < -1) or np.any(receiver >= wet.size):
        raise ValueError("receiver must index a local wet column or be -1")
    if np.any(receiver[~wet.ravel()] != -1) or np.any(~wet.ravel()[receiver[active]]):
        raise ValueError("receivers must connect wet columns only")
    ids = np.flatnonzero(active)
    if np.any(ids == receiver[active]):
        raise ValueError("receiver cannot point to itself")
    stage = product["surface_y"].ravel()
    if np.any(stage[receiver[active]] > stage[ids]):
        raise ValueError("receivers cannot require uphill flow")
    _validate_sections(product)
    _validate_receiver_paths(wet, receiver)
    if np.any(product["bed_material"] > 5) or np.any(product["bank_material"] > 5):
        raise ValueError("unknown material code")
    if np.any(product["aquatic_plant"] > 3) or np.any(product["aquatic_plant"][~wet]):
        raise ValueError("invalid aquatic vegetation")
    top = product["waterfall_top_y"]
    curtain = top != DRY
    if np.any(curtain & ~wet) or np.any(top[curtain] <= product["surface_y"][curtain]):
        raise ValueError("curtain must extend above an existing water column")
    return shape


def _manifest_json(manifest):
    """Bind metadata to the NPZ without introducing a circular file hash."""
    return json.dumps({key: value for key, value in manifest.items()
                       if key != "product_sha256"},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def save_product(path, product, report, settings, provenance):
    """Publish an exclusive product; invalid physical plans remain diagnostic."""
    validate(product)
    validate_report(product,report)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = path.with_suffix(".json")
    if path.exists() or manifest_path.exists():
        raise FileExistsError("choose a new run name; products are immutable")
    manifest = {"engine": ENGINE, "schema_version": SCHEMA_VERSION,
                "settings": asdict(settings), "provenance": provenance,
                "report": report, "world_written": False,
                "visual_acceptance": "pending", "post_tick_acceptance": "pending"}
    metadata = _manifest_json(manifest)
    with path.open("xb") as handle:
        np.savez_compressed(handle, **product,
                            engine=np.asarray(ENGINE), schema_version=np.asarray(SCHEMA_VERSION),
                            manifest_json=np.asarray(metadata))
    with path.open("rb") as handle:
        manifest["product_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    with manifest_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
    return manifest


def load_product(path):
    """Read the versioned package only after checking its recorded bytes."""
    path = Path(path)
    manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("engine") != ENGINE or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported water product schema")
    with path.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != manifest.get("product_sha256"):
        raise ValueError("water product hash does not match its manifest")
    with np.load(path, allow_pickle=False) as arrays:
        if arrays["engine"].item() != ENGINE or arrays["schema_version"].item() != SCHEMA_VERSION:
            raise ValueError("embedded water product schema disagrees with manifest")
        if "manifest_json" not in arrays or arrays["manifest_json"].shape != ():
            raise ValueError("water product has no embedded manifest metadata")
        if arrays["manifest_json"].item() != _manifest_json(manifest):
            raise ValueError("water product manifest metadata disagrees with its embedded copy")
        product = {key: arrays[key] for key in ("bounds", *FIELDS)}
    validate(product)
    validate_report(product,manifest['report'])
    return product, manifest

"""Compile and inspect independent water scenes; no world writes."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import numpy as np


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def source_fingerprint():
    return {p.name:digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))}


# Record before importing compiler modules. A concurrent source edit must not
# label an already-loaded implementation with the hash of different code.
LOADED_SOURCE = source_fingerprint()

from .blocks import iter_column_blocks
from .engine import compile_scene
from .model import Settings, load_product, save_product
from .topology import build_network, candidate_routes

ROOT = Path(__file__).resolve().parents[1]


def compile_from_inputs(terrain_path, sources_path, bounds, *, corridor=3):
    """Consume raw terrain/source data only. Source fields are decoded once."""
    if source_fingerprint() != LOADED_SOURCE:
        raise RuntimeError("water compiler source changed; restart the command")
    terrain_path, sources_path = Path(terrain_path), Path(sources_path)
    input_hashes = (digest(terrain_path), digest(sources_path))
    terrain = np.load(terrain_path, mmap_mode="r", allow_pickle=False)
    x0,x1,z0,z1 = map(int,bounds)
    if terrain.ndim != 2 or terrain.dtype != np.int16:
        raise ValueError("terrain must be a 2D int16 raster in world block heights")
    if not (0 <= x0 < x1 <= terrain.shape[1] and 0 <= z0 < z1 <= terrain.shape[0]):
        raise ValueError("scene bounds must lie inside the input terrain")
    base = np.array(terrain[z0:z1,x0:x1])
    with np.load(sources_path, allow_pickle=False) as sources:
        body = sources["waterbody_mask"]
        if body.shape != terrain.shape or body.dtype != np.bool_:
            raise ValueError("raw waterbody mask must match terrain and use bool")
        body = body[z0:z1,x0:x1].copy()
        network = build_network(sources["points_x"],sources["points_z"],sources["offsets"],
                                sources["kind"],sources["width_m"],bounds=bounds)
    # Width conversion is explicit: the project uses four metres per block.
    # Preserve source width rather than narrowing the mapped river to pass
    # a solver threshold. Infeasible width/terrain combinations are reported.
    radii = np.maximum(np.floor((network.widths/4-1)/2),0).astype(int)
    settings = Settings()
    attempts = []
    best_score = None
    for strategy,routes in candidate_routes(network,base,origin=(x0,z0),corridor=corridor):
        proposed, measured = compile_scene(base,body,routes,origin=(x0,z0),radii=radii,
                                           route_nodes=network.edges,settings=settings)
        failures = measured['violations']
        attempts.append({'strategy':strategy,'violations':failures})
        topology = failures.get('unplanned_water_contact',0)+failures.get('ambiguous_route_crossing',0)
        score = (bool(topology),int(np.count_nonzero(proposed['diagnostic_flags'])))
        if best_score is None or score < best_score:
            product,report,best_score = proposed,measured,score
            selected = strategy
        if not failures:
            break
    report['routing_strategy'] = selected
    report['routing_attempts'] = attempts
    report["source_nodes"] = len(network.nodes)
    report["source_edges"] = len(network.edges)
    report["maximum_source_width_metres"] = float(network.widths.max()) if len(network.widths) else 0.0
    # Exercise the independent sink for every planned water/bank column.
    # Invalid source/plant/curtain combinations must not hide in a green report.
    emitted = 0
    for z,x in np.argwhere(product["water_mask"] | product["bank_mask"]):
        emitted += len(list(iter_column_blocks(product,int(x),int(z))))
    report["planned_blocks"] = emitted
    if source_fingerprint() != LOADED_SOURCE:
        raise RuntimeError("water compiler source changed during compilation; product not published")
    if input_hashes != (digest(terrain_path), digest(sources_path)):
        raise RuntimeError("water inputs changed during compilation; product not published")
    provenance = {"created_utc":datetime.now(timezone.utc).isoformat(),
                  "terrain":str(terrain_path.resolve()),"terrain_sha256":input_hashes[0],
                  "sources":str(sources_path.resolve()),"sources_sha256":input_hashes[1],
                  "source_code_sha256":LOADED_SOURCE,
                  "bounds":list(bounds),"routing_corridor_blocks":corridor,
                  "metres_per_block":4,"settings":asdict(settings)}
    return product, report, settings, provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command",required=True)
    compile_command = commands.add_parser("compile",help="create a scene NPZ and diagnostic JSON")
    compile_command.add_argument("--terrain",type=Path,default=ROOT/"terrain_y.npy")
    compile_command.add_argument("--sources",type=Path,default=ROOT/"water_sources.npz")
    compile_command.add_argument("--bounds",nargs=4,type=int,required=True,metavar=("X0","X1","Z0","Z1"))
    compile_command.add_argument("--corridor",type=float,default=3)
    compile_command.add_argument("--output",type=Path,required=True)
    inspect = commands.add_parser("inspect",help="verify a product and show one world-coordinate column")
    inspect.add_argument("product",type=Path)
    inspect.add_argument("--at",nargs=2,type=int,metavar=("X","Z"))
    scenarios = commands.add_parser("scenarios",help="compile the four known review locations to a new directory")
    scenarios.add_argument("--terrain",type=Path,default=ROOT/"terrain_y.npy")
    scenarios.add_argument("--sources",type=Path,default=ROOT/"water_sources.npz")
    scenarios.add_argument("--output-dir",type=Path,required=True)
    scenarios.add_argument("--corridor",type=float,default=3)
    args = parser.parse_args(argv)
    if args.command == "scenarios":
        from .scenarios import run_scenarios
        summary = run_scenarios(args.output_dir, args.terrain, args.sources,
                                compile_from_inputs, corridor=args.corridor)
        print(json.dumps(summary,indent=2,ensure_ascii=False))
        return 0 if summary["compile_gate_passed"] else 2
    if args.command == "compile":
        if args.output.suffix != ".npz":
            parser.error("--output must end in .npz")
        if args.output.exists() or args.output.with_suffix(".json").exists():
            parser.error("output already exists; choose a new candidate name")
        start = time.monotonic()
        product,report,settings,provenance = compile_from_inputs(
            args.terrain,args.sources,args.bounds,corridor=args.corridor)
        save_product(args.output,product,report,settings,provenance)
        print(json.dumps({"product":str(args.output.resolve()),"elapsed_seconds":round(time.monotonic()-start,3),
                          **report},indent=2,ensure_ascii=False))
        return 0 if report["ready_for_world_write"] else 2
    product,manifest = load_product(args.product)
    result = {"engine":manifest["engine"],"report":manifest["report"]}
    if args.at:
        x,z = args.at
        x0,_,z0,_ = map(int,product["bounds"])
        result["column"] = {"x":x,"z":z,"blocks":list(iter_column_blocks(product,x-x0,z-z0))}
        from .diagnostics import FLAGS
        flags = int(product['diagnostic_flags'][z-z0,x-x0])
        result['column']['diagnostics'] = [name for name,bit in FLAGS.items() if flags & bit]
        result['column']['river_pool'] = bool(product['river_pool_mask'][z-z0,x-x0])
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

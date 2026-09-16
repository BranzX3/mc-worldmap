"""Check or run a bounded build -> paint -> reopen/readback trial.

Default is read-only. --run backs up the explicit save before any world write.
All three stages execute the frozen candidate scripts, using one patch product.
"""
import argparse
import datetime
import importlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import numpy as np

import baseline_snapshot as B
import config as C
import golden_patches as G
from preflight_environment import preflight, is_ready
from pipeline_progress import world_session_locked

ROOT = Path(__file__).resolve().parent
DEFAULT_PATCHES = ("lake_mouth", "hill_junction", "steep_stream", "player_liked")


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def trial_bounds(cx, cz, size, bounds, margin=G.CORE_MARGIN):
    if size <= 0 or size % 2:
        raise ValueError("size must be positive and even")
    x0, x1, z0, z1 = map(int, bounds)
    box = (cx-size//2, cx+size//2, cz-size//2, cz+size//2)
    if not (x0+margin <= box[0] < box[1] <= x1-margin
            and z0+margin <= box[2] < box[3] <= z1-margin):
        raise ValueError("paint box must stay inside the audited hydrology core")
    return box


def verify_artifact(root, evidence, path):
    path = Path(path)
    key = path.relative_to(root).as_posix()
    expected = evidence["artifact_sha256"].get(key)
    if not expected or B.digest(path) != expected:
        raise ValueError("unverified or changed artifact: " + key)


def make_plan(root, candidate, world, names=DEFAULT_PATCHES, size=256):
    root, world = Path(root).resolve(), Path(world).resolve()
    snapshot = B.snapshot_path(root, candidate)
    problems = B.verify(root, candidate, current=True)
    if problems:
        raise ValueError("candidate differs from workspace: " + "; ".join(problems))
    evidence = json.loads((root / "docs/reviews" / (candidate + ".evidence.json")).read_text(encoding="utf-8"))
    if evidence["failures"] or evidence["errors"] or evidence["skipped"]:
        raise ValueError("candidate suite did not pass completely")
    tests_log = snapshot / "tests.log"
    verify_artifact(root, evidence, tests_log)
    log = tests_log.read_text(encoding="utf-8")
    if not re.search(r"^OK$", log, re.M) or re.search(r"^FAILED|^OK \(", log, re.M):
        raise ValueError("full candidate suite has no successful result")
    products = root / "golden" / candidate
    metrics_path, run_path = products / "metrics.json", products / "run.json"
    verify_artifact(root, evidence, metrics_path)
    verify_artifact(root, evidence, run_path)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    run = json.loads(run_path.read_text(encoding="utf-8"))
    specs = {spec["name"]: spec for spec in G.GOLDEN_PATCHES}
    if set(metrics) != set(specs) or set(run["fingerprints"]) != set(specs):
        raise ValueError("candidate must contain all golden patches")
    if not names or len(set(names)) != len(names) or any(n not in specs for n in names):
        raise ValueError("select distinct, known golden patch names")
    terrain = np.load(root / "terrain_y.npy", mmap_mode="r")
    selected = []
    for name, spec in specs.items():
        if run["fingerprints"][name] != G.shape_fingerprint(spec):
            raise ValueError("stale shape fingerprint: " + name)
        path = products / "patches" / Path(G.patch_path(spec)).name
        verify_artifact(root, evidence, path)
        with np.load(path, allow_pickle=False) as patch:
            x0, x1, z0, z1 = map(int, patch["bounds"])
            measured = G.patch_metrics(patch, terrain[z0:z1, x0:x1])
            if measured != metrics[name]:
                raise ValueError("metrics do not match the actual product: " + name)
            if any(measured[k] for k in G.MUST_BE_ZERO):
                raise ValueError("hard geometry error: " + name)
            if any(measured[k] > .05 for k in ("canyon_share_excess", "bank_unwalkable_excess")):
                raise ValueError("morphology threshold exceeded: " + name)
            if name in names:
                box = trial_bounds(spec["x"], spec["z"], size, patch["bounds"])
                wet = patch["water_mask"][box[2]-z0:box[3]-z0, box[0]-x0:box[1]-x0]
                if not wet.any():
                    raise ValueError("trial would not exercise any water: " + name)
                selected.append({"name": name, "x": spec["x"], "z": spec["z"],
                                 "size": size, "bounds": box, "hydrology_patch": str(path),
                                 "water_columns": int(wet.sum()), "sha256": B.digest(path)})
    selected.sort(key=lambda item: list(names).index(item["name"]))
    return {"candidate": candidate, "world": str(world), "patches": selected,
            "frozen_runner": str(snapshot / "workspace/paint_trial.py"),
            "compare_regressions": evidence["compare_regressions"],
            "review": str(root / "docs/reviews" / (candidate + ".md"))}


def backup_save(world, target):
    world, target = Path(world).resolve(), Path(target).resolve()
    if target.is_relative_to(world) or world.is_relative_to(target):
        raise ValueError("backup must be separate from the source save")
    if world_session_locked(str(world)):
        raise ValueError("source save is open or locked")
    files = sorted(p for p in world.rglob("*") if p.is_file())
    if not (world / "level.dat").is_file():
        raise ValueError("not a Minecraft save")
    if any(p.is_symlink() for p in world.rglob("*")):
        raise ValueError("save contains symlinks; review before copying")
    target.mkdir(parents=True, exist_ok=False)
    records = {}
    for source in files:
        relative = source.relative_to(world)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        before = B.digest(source)
        shutil.copy2(source, destination)
        if B.digest(destination) != before or B.digest(source) != before:
            raise ValueError("save changed during backup: " + str(relative))
        records[relative.as_posix()] = before
    current = {p.relative_to(world).as_posix(): B.digest(p) for p in world.rglob("*") if p.is_file()}
    if current != records or world_session_locked(str(world)):
        raise ValueError("save changed or opened during backup")
    return records


def run_step(step, world, item):
    # Configure this child only; never rewrite config.py or rely on the user's
    # default save. The runner controls arguments and never uses --reset-terrain
    # (which would spawn an unconfigured child). Build is its own checked step.
    C.WORLD_PATH = str(Path(world).resolve())
    environment = preflight(C.WORLD_PATH)
    if not is_ready(environment):
        raise SystemExit(json.dumps(environment, ensure_ascii=False, indent=2))
    module = {"build": "build_terrain", "paint": "paint_surface", "readback": "check_world_water"}[step]
    coordinates = [str(item["x"]), str(item["z"])]
    sys.argv = [module, *( ["--at", *coordinates, "--size", str(item["size"])]
                         if step == "readback" else ["--patch", *coordinates, str(item["size"])] ),
                "--hydrology-patch", item["hydrology_patch"]]
    importlib.import_module(module).main()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "_step":
        step, world, item_json = sys.argv[2:5]
        run_step(step, world, json.loads(item_json))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--world", required=True, help="Explicit Minecraft save directory")
    parser.add_argument("--patches", nargs="+", default=list(DEFAULT_PATCHES))
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--run", action="store_true", help="Back up, build, paint, then reopen/read back")
    args = parser.parse_args()
    plan = make_plan(ROOT, args.candidate, args.world, args.patches, args.size)
    environment = preflight(args.world, full=True)
    plan["environment"] = environment
    plan["ready"] = is_ready(environment)
    print(json.dumps(plan, indent=2, ensure_ascii=False), flush=True)
    if not plan["ready"]:
        return 1
    if not args.run:
        return 0
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "archive/paint_trials" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "plan.json", plan)
    print("Backing up save to", directory / "save-before", flush=True)
    records = backup_save(args.world, directory / "save-before")
    write_json(directory / "backup-manifest.json", {"source": plan["world"], "files": records})
    steps = []
    for item in plan["patches"]:
        for step in ("build", "paint", "readback"):
            path = directory / f"{item['name']}-{step}.log"
            print(item["name"], step, "->", path, flush=True)
            command = [sys.executable, "-u", "-X", "utf8", plan["frozen_runner"], "_step",
                       step, plan["world"], json.dumps(item)]
            with path.open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=str(Path(plan["frozen_runner"]).parent),
                                        stdout=log, stderr=subprocess.STDOUT)
            steps.append({"patch": item["name"], "step": step, "exit_code": result.returncode,
                          "log": str(path), "log_sha256": B.digest(path)})
            write_json(directory / "result.json", {"candidate": args.candidate, "steps": steps,
                       "complete": len(steps) == len(plan["patches"])*3 and result.returncode == 0})
            if result.returncode:
                print("Stopped after failed step; inspect", path, flush=True)
                return result.returncode
    print("Build/paint/reopen readback completed:", directory, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

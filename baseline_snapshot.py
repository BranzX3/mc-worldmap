"""Capture a local, self-contained workspace before a golden baseline run.

Usage: python baseline_snapshot.py capture <tag>
       python baseline_snapshot.py verify <tag> [--current]

Run golden_patches.py from the captured workspace so live edits cannot change
its inputs. Snapshots never include saves, venvs, caches or personal settings.
"""
import argparse
import datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REQUIRED_INPUTS = ("terrain_y.npy", "water_sources.npz", "heightmap.png",
                   "heightmap_meta.json", "landcover.npz", "terrain_sub.npy")


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def inventory(root):
    """Explicit scope, including untracked work, without walking caches/venvs."""
    result = {}
    for path in root.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if path.suffix in (".py", ".ps1") or name.startswith("requirements") or name == ".gitignore":
            result[name] = "code"
        elif path.suffix == ".md":
            result[name] = "docs"
        elif path.suffix in (".npy", ".npz", ".png", ".json") and "progress" not in name:
            result[name] = "input"
    for folder, suffixes, kind in (
        ("tests", {".py"}, "code"), ("water_v2", {".py"}, "code"),
        ("docs", {".md"}, "docs"),
        ("trees", {".nbt", ".schem", ".schematic", ".litematic"}, "asset"),
    ):
        for path in (root / folder).rglob("*"):
            if path.is_file() and path.suffix in suffixes:
                result[path.relative_to(root).as_posix()] = kind
    return dict(sorted(result.items()))


def snapshot_path(root, tag):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", tag):
        raise ValueError("tag must contain only letters, numbers, underscores or hyphens")
    return root / "archive" / "baselines" / tag


def git_output(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def capture(root, tag):
    missing = [name for name in REQUIRED_INPUTS if not (root / name).is_file()]
    if missing:
        raise ValueError("Missing baseline inputs: " + ", ".join(missing))
    target = snapshot_path(root, tag)
    target.mkdir(parents=True, exist_ok=False)
    workspace = target / "workspace"
    workspace.mkdir()
    items = inventory(root)
    records = {}
    for name, kind in items.items():
        source, copied = root / name, workspace / name
        copied.parent.mkdir(parents=True, exist_ok=True)
        before = digest(source)
        shutil.copy2(source, copied)
        if digest(copied) != before or digest(source) != before:
            raise RuntimeError("File changed during capture: " + name)
        records[name] = {"kind": kind, "sha256": before, "bytes": copied.stat().st_size}
    # Preserve both staged and unstaged changes; workspace also contains new files.
    patch = git_output(root, "diff", "--binary", "HEAD")
    (target / "working-tree.patch").write_bytes(patch)
    manifest = {
        "schema": 1, "tag": tag,
        "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_head": git_output(root, "rev-parse", "HEAD").decode().strip(),
        "git_status": git_output(root, "status", "--porcelain").decode(),
        "python": sys.version, "executable": sys.executable,
        "packages": dict(sorted((d.metadata["Name"], d.version)
                                for d in importlib.metadata.distributions())),
        "working_tree_patch_sha256": hashlib.sha256(patch).hexdigest(),
        "files": records,
    }
    # Recheck the whole source set; success is recorded only after capture is consistent.
    if inventory(root) != items:
        raise RuntimeError("Workspace file set changed during capture")
    for name, record in records.items():
        if digest(root / name) != record["sha256"]:
            raise RuntimeError("Workspace changed during capture: " + name)
    (target / "snapshot.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    return target


def verify(root, tag, current=False):
    target = snapshot_path(root, tag)
    manifest = json.loads((target / "snapshot.json").read_text(encoding="utf-8"))
    problems = []
    for name, record in manifest["files"].items():
        path = target / "workspace" / name
        if not path.is_file() or digest(path) != record["sha256"]:
            problems.append("snapshot changed: " + name)
        if current and record["kind"] != "docs":
            live = root / name
            if not live.is_file() or digest(live) != record["sha256"]:
                problems.append("current workspace changed: " + name)
    if digest(target / "working-tree.patch") != manifest["working_tree_patch_sha256"]:
        problems.append("working-tree.patch changed")
    if current:
        expected = {n for n, r in manifest["files"].items() if r["kind"] != "docs"}
        actual = {n for n, kind in inventory(root).items() if kind != "docs"}
        problems.extend("current file added/removed: " + n for n in sorted(actual ^ expected))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "verify"))
    parser.add_argument("tag")
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()
    if args.command == "capture":
        print(capture(ROOT, args.tag))
    else:
        problems = verify(ROOT, args.tag, current=args.current)
        print("\n".join(problems) if problems else "Snapshot SHA-256 verification: PASS")
        return int(bool(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

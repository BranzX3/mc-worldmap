"""Fail-closed preflight for the real build/paint acceptance loop.

This module intentionally has no Amulet/scipy import at module load time.  It
must remain runnable in a broken environment so the operator gets one clear
diagnosis before ``build_terrain.py`` or ``paint_surface.py`` imports their
heavy runtime dependencies.

Use::

    python preflight_environment.py
    python preflight_environment.py --world <save>

Exit status is non-zero when Python, actual dependency imports, game schema,
or save access is not ready. No save bytes are changed by these probes.
"""

import argparse
import contextlib
import importlib
import importlib.metadata
import json
import os
import sys
import zipfile

import config as C
from pipeline_progress import world_session_locked


REQUIRED_MODULES = {
    "numpy": "numpy",
    "scipy": "scipy",
    "amulet": "amulet-core",
    "amulet_nbt": "amulet-nbt",
    "PIL": "pillow",
    "PyMCTranslate": "PyMCTranslate",
}
GIS_MODULES = {
    "osmnx": "osmnx", "geopandas": "geopandas", "rasterio": "rasterio",
    "shapely": "shapely", "pyproj": "pyproj",
}


def dependency_report(full=False):
    """Exercise real imports: an installed package may still have a broken DLL."""
    packages = {**REQUIRED_MODULES, **(GIS_MODULES if full else {})}
    result = {}
    for module, package in packages.items():
        try:
            # Keep CLI stdout machine-readable even if a dependency prints.
            with contextlib.redirect_stdout(sys.stderr):
                importlib.import_module(module)
            result[module] = {
                "package": package, "available": True,
                "version": importlib.metadata.version(package), "error": None,
            }
        except Exception as exc:
            result[module] = {
                "package": package, "available": False, "version": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return result


def dependency_status():
    """Compatibility view for callers that only need import availability."""
    return {name: item["available"] for name, item in dependency_report().items()}


def client_jar_status():
    """Read the installed game's version and dimension schema, without writing."""
    from make_world_datapack import find_client_jar

    path = None
    try:
        path = find_client_jar()
        with zipfile.ZipFile(path) as archive:
            version = json.loads(archive.read("version.json"))
            dimension = json.loads(archive.read(
                "data/minecraft/dimension_type/overworld.json"
            ))
        # These are consumed by the current datapack writer.
        data_major = int(version["pack_version"]["data_major"])
        for key in ("min_y", "height", "logical_height"):
            int(dimension[key])
        return {
            "available": True, "path": path, "version": version["id"],
            "data_major": data_major, "error": None,
        }
    except (Exception, SystemExit) as exc:
        return {"available": False, "path": path,
                "error": f"{type(exc).__name__}: {exc}"}


def world_access_status(world_path):
    """Probe existing files with read/write handles, without changing bytes.

    This checks level.dat and one existing region, not every region's ACL.
    session.lock is checked separately using the writer's fail-closed gate.
    """
    checked = []
    try:
        paths = [os.path.join(world_path, "level.dat")]
        namespace, dimension = C.DIMENSION.split(":", 1)
        candidates = [os.path.join(world_path, "dimensions", namespace, dimension, "region")]
        legacy = {"minecraft:overworld": "", "minecraft:the_nether": "DIM-1",
                  "minecraft:the_end": "DIM1"}
        if C.DIMENSION in legacy:
            candidates.append(os.path.join(world_path, legacy[C.DIMENSION], "region"))
        for region_dir in candidates:
            try:
                with os.scandir(region_dir) as entries:
                    regions = sorted(entry.path for entry in entries
                                     if entry.name.endswith(".mca") and entry.is_file())
                sample = regions[0] if regions else None
                break
            except FileNotFoundError:
                continue
        else:
            raise FileNotFoundError("No region directory for " + C.DIMENSION)
        if sample:
            paths.append(sample)
        for path in paths:
            fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
            try:
                os.read(fd, 1)
            finally:
                os.close(fd)
            checked.append(path)
        return {"accessible": True, "checked_files": checked,
                "region_directory": region_dir, "error": None}
    except OSError as exc:
        return {"accessible": False, "checked_files": checked,
                "error": f"{type(exc).__name__}: {exc}"}


def preflight(world_path=None, full=False):
    """Return a JSON-serialisable fail-closed environment report."""
    world_path = os.path.abspath(world_path or C.WORLD_PATH)
    details = dependency_report(full=full)
    dependencies = {name: item["available"] for name, item in details.items()}
    return {
        "python": {"executable": sys.executable, "version": sys.version.split()[0],
                   "supported": sys.version_info[:2] == (3, 11)},
        "world_path": world_path,
        "world_exists": os.path.isdir(world_path),
        "world_session_locked": world_session_locked(world_path),
        "world_access": world_access_status(world_path),
        "client_jar": client_jar_status(),
        "dependencies": dependencies,
        "dependency_details": details,
        "missing_packages": [
            details[module]["package"]
            for module, available in dependencies.items()
            if not available
        ],
    }


def is_ready(report):
    """Return true only when every hard precondition is explicitly proven."""
    return (
        report["world_exists"]
        and not report["world_session_locked"]
        and not report["missing_packages"]
        and report["python"]["supported"]
        and report["client_jar"]["available"]
        and report["world_access"]["accessible"]
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", help="Save directory (default: config.WORLD_PATH)")
    parser.add_argument("--full", action="store_true", help="Also import GIS dependencies")
    args = parser.parse_args(argv)
    report = preflight(args.world, full=args.full)
    report["ready"] = is_ready(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

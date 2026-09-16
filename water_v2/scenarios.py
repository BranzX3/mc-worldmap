"""Reproducible real-data acceptance scenes, independent of legacy golden runs."""
import json
from pathlib import Path
import time

from . import ENGINE, SCHEMA_VERSION
from .model import load_product, save_product


SCENARIOS = {
    "lake_mouth": (2242, 3057),
    "hill_junction": (3344, 480),
    "steep_stream": (5792, 5384),
    "player_liked": (6165, 6068),
}

REVIEW_CHECKS = {
    "lake_mouth": ["ระดับผืนน้ำและการเชื่อมปากน้ำ", "ตลิ่งไม่เป็นกำแพง", "ความลึกต่อเนื่องจากฝั่ง"],
    "hill_junction": ["ทางน้ำต่อกันตรงจุดบรรจบ", "ไม่มีร่องขุดลึกเพื่อฝืน DEM", "ทางลงและข้ามน้ำ"],
    "steep_stream": ["รักษาแนวลาดและน้ำตกจริง", "ทางน้ำไม่ลัดข้ามโค้ง", "น้ำไหลต่อเนื่องหลัง tick"],
    "player_liked": ["รักษาลักษณะภูมิประเทศที่ชอบ", "แอ่งและลำธารเชื่อมอย่างมีเหตุผล", "วัสดุและพืชไม่กลบรูปทรง"],
}


def run_scenarios(directory, terrain, sources, compiler, *, corridor=3):
    """Publish four immutable products plus concrete coordinate review JSON.

    Physical failure remains a saved diagnostic, never a skipped scenario.
    Reopen every product and verify its bytes before counting it as checked.
    This command has no world-writing path.
    """
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    summary = {"engine": ENGINE, "schema_version": SCHEMA_VERSION,
               "candidate": directory.name, "domain": "bounded_scene",
               "world_written": False, "compile_gate_passed": False,
               "complete": False, "cases": {}}
    review = {"candidate": directory.name, "engine": ENGINE, "world_written": False,
              "instruction": "รายการนี้เป็นแผนตรวจระบบใหม่ ยังไม่มีฉากในเกมให้ตรวจ; เซฟทดลองเดิมเป็นน้ำระบบเก่า",
              "locations": []}
    for name, (x, z) in SCENARIOS.items():
        started = time.monotonic()
        bounds = (x-192, x+192, z-192, z+192)
        product, report, settings, provenance = compiler(terrain, sources, bounds, corridor=corridor)
        if tuple(map(int, product["bounds"])) != bounds:
            raise ValueError("compiler returned a product for different scene bounds")
        path = directory/(name+".npz")
        save_product(path, product, report, settings, provenance)
        _, manifest = load_product(path)
        summary["cases"][name] = {
            "center": {"x": x, "z": z}, "bounds": list(bounds),
            "product": str(path), "product_sha256": manifest["product_sha256"],
            "product_readback": "passed", "elapsed_seconds": round(time.monotonic()-started, 3),
            "scenario_gate_passed": report["ready_for_world_write"] and report["water_cells"] > 0,
            **report,
        }
        review["locations"].append({
            "id": name, "coordinates": {"x": x, "y": None, "z": z},
            "compile_gate": "passed" if report["ready_for_world_write"] else "failed",
            "violations": report["violations"], "example_coordinates": report.get("examples", {}),
            "world_review": "not_available_until_new_world_trial",
            "checks": [{"item": item, "status": "not_tested", "comment": "", "screenshot": ""}
                       for item in REVIEW_CHECKS[name]],
            "overall_comment": "",
        })
        # Partial progress is explicitly incomplete if a later compile aborts.
        _write_json(directory/"summary.json", summary)
        _write_json(directory/"review.json", review)
    summary["complete"] = True
    summary["compile_gate_passed"] = all(case["scenario_gate_passed"] for case in summary["cases"].values())
    _write_json(directory/"summary.json", summary)
    return summary


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")

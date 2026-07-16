"""Large nested YAML auto-compile regression.

Run from the project root:
    python tests/test_large_complex_auto.py
"""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.yaml_config_engine.comparison import strict_equal
from src.yaml_config_engine.diff_compiler import DiffCompiler
from src.yaml_config_engine.engine import YamlPatchEngine
from src.yaml_config_engine.yamlio import load_one


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    fixture = ROOT / "tests" / "fixtures" / "large-complex"
    before_path = fixture / "before.yaml"
    after_path = fixture / "after.yaml"
    before_lines = sum(1 for _ in before_path.open(encoding="utf-8"))
    after_lines = sum(1 for _ in after_path.open(encoding="utf-8"))
    require(before_lines >= 2000 and after_lines >= 2000, "fixture must be at least 2,000 lines")

    before = load_one(before_path)
    after = load_one(after_path)
    started = time.monotonic()
    result = DiffCompiler(optimization_timeout_seconds=5, optimization_max_candidates=2000).compile(before, after)
    elapsed = time.monotonic() - started
    require(result.verified, "large auto compile must verify")
    operations = result.config["operations"]
    require(len(operations) <= 4, f"expected strongly merged config, got {len(operations)} operations")
    require(all("paths" in op for op in operations), "mixed-depth branches should remain compact multi-pattern paths")
    for op in operations:
        require(op["paths"] == ["$/*/flat-fabs/*", "$/*/regions/*/*"], f"unexpected compressed paths: {op['paths']}")

    applied = YamlPatchEngine().apply_document(deepcopy(before), result.config, track_no_effect=False)
    require(strict_equal(applied, after), "large merged config must replay exactly")
    print(f"PASS: large complex auto compile lines={before_lines}, operations={len(operations)}, elapsed={elapsed:.3f}s")


if __name__ == "__main__":
    main()

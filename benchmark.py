from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from config_tool_api import ConfigTool


def write_large_yaml(path: Path, zones: int = 40, services: int = 12, timeout: int = 30) -> None:
    lines = ["# benchmark generated YAML", "platform:", "  zones:"]
    for z in range(zones):
        lines += [f"    zone-{z:03d}:", "      policy:", f"        timeout: {timeout}", "      services:"]
        for s in range(services):
            lines += [f"        - name: svc-{s:03d}", "          enabled: true", f"          port: {8000+s}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    tool = ConfigTool()
    with TemporaryDirectory(prefix="config-tool-benchmark-") as td:
        root = Path(td)
        before = root / "before.yaml"
        after = root / "after.yaml"
        patch = root / "patch.yaml"
        output = root / "output.yaml"
        write_large_yaml(before, timeout=30)
        write_large_yaml(after, timeout=45)

        started = time.perf_counter(); compile_result = tool.compile(before, after, patch); compile_s = time.perf_counter() - started
        started = time.perf_counter(); apply_result = tool.apply(before, patch, output); apply_s = time.perf_counter() - started
        started = time.perf_counter(); verify_result = tool.verify(before, patch, after); verify_s = time.perf_counter() - started
        operations = len(compile_result.data.get("config", {}).get("operations", []))
        payload = {
            "release": "0.10.0-rc15",
            "input_lines": len(before.read_text(encoding="utf-8").splitlines()),
            "compile_seconds": round(compile_s, 6),
            "apply_seconds": round(apply_s, 6),
            "verify_seconds": round(verify_s, 6),
            "operations": operations,
            "compile_verified": bool(compile_result.verified),
            "apply_changed": bool(apply_result.changed),
            "verify_passed": bool(verify_result.verified),
        }
        Path("benchmark-report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0 if payload["compile_verified"] and payload["verify_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

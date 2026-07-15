from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(name: str, command: list[str]) -> dict:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    print(f"[{name}] {'PASS' if completed.returncode == 0 else 'FAIL'}")
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    return {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
    }


def main() -> int:
    checks = [
        run("compileall", [sys.executable, "-m", "compileall", "-q", "."]),
        run("self-test", [sys.executable, "self_test.py"]),
        run("yaml-cli-help", [sys.executable, "yaml_config_tool.py", "--help"]),
        run("xml-cli-help", [sys.executable, "xml_config_tool.py", "--help"]),
        run("mixed-cli-help", [sys.executable, "config_tool.py", "--help"]),
    ]
    payload = {
        "release": "0.10.0-rc1",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "checks": checks,
        "passed": all(item["returncode"] == 0 for item in checks),
    }
    report = ROOT / "release-check-report.json"
    report.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {report}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

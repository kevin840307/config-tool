#!/usr/bin/env python3
"""Direct runner. No package installation is required.

Examples:
  python yaml_config_tool.py compile-folder before after generated
  python yaml_config_tool.py apply-folder before generated output
  python yaml_config_tool.py verify-folder before generated after
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from yaml_config_engine.cli import main

if __name__ == '__main__':
    raise SystemExit(main())

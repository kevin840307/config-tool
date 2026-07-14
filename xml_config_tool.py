#!/usr/bin/env python3
"""Direct XML runner; no package installation is required."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
SRC=ROOT/'src'
if str(SRC) not in sys.path: sys.path.insert(0,str(SRC))
from xml_config_engine.cli import main
if __name__=='__main__': raise SystemExit(main())

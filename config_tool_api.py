"""Public Python API entry point.

Usage: from public_api import ConfigTool, ConfigToolResult
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from public_api import ConfigTool, ConfigToolResult
__all__ = ['ConfigTool', 'ConfigToolResult']

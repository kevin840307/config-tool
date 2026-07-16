"""Format-preserving XML configuration engine.

This package is intentionally independent from yaml_config_engine so XML support
cannot change YAML parsing, ordering, comments, or serialization behaviour.
"""
from .engine import XmlPatchEngine, XmlApplyResult

__all__ = ["XmlPatchEngine", "XmlApplyResult"]

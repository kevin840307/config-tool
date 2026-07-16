from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError

from src.yaml_config_engine.engine import YamlPatchEngine
from src.yaml_config_engine.errors import ConfigError
from src.yaml_config_engine.template import render_value
from src.yaml_config_engine.yamlio import load_one


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_raises(expected, action, message: str) -> None:
    try:
        action()
    except expected:
        return
    except Exception as exc:
        raise AssertionError(f"{message}: expected {expected.__name__}, got {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"{message}: expected {expected.__name__}")


def test_duplicate_yaml_key() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "duplicate.yaml"
        path.write_text("app:\n  port: 8080\n  port: 9090\n", encoding="utf-8")
        require_raises(DuplicateKeyError, lambda: load_one(path), "duplicate YAML keys must fail")


def test_missing_template_variable() -> None:
    require_raises(Exception, lambda: render_value("{{ missing_variable }}", {}), "missing template variable must fail")


def test_non_unique_match_is_not_silent() -> None:
    document = {"versions": [{"name": "v1", "enabled": False}, {"name": "v1", "enabled": False}]}
    config = {"version": 1, "operations": [{"op": "update_item", "path": "$/versions", "match": {"name": "v1"}, "set": {"enabled": True}, "missing": "error"}]}
    require_raises(Exception, lambda: YamlPatchEngine().apply_document(document, config), "non-unique match must fail by default")


def test_selector_create_rejected() -> None:
    document = {"apps": {}}
    config = {"version": 1, "operations": [{"op": "set", "path": "$/apps/*/enabled", "value": True, "missing": "create"}]}
    require_raises(ConfigError, lambda: YamlPatchEngine().apply_document(document, config), "selector missing:create must fail")


def test_invalid_regex_rejected() -> None:
    document = {"apps": {"api": {"enabled": False}}}
    config = {"version": 1, "operations": [{"op": "set", "path": "$/apps", "key_pattern": "[", "pattern_type": "regex", "value": True}]}
    require_raises(Exception, lambda: YamlPatchEngine().apply_document(document, config), "invalid regex must fail")


def test_out_of_range_index_rejected() -> None:
    document = {"ports": [80, 443]}
    config = {"version": 1, "operations": [{"op": "insert_at", "path": "$/ports", "index": 99, "value": 8080}]}
    require_raises(Exception, lambda: YamlPatchEngine().apply_document(document, config), "out-of-range insert must fail without policy")


def test_wrong_container_type_rejected() -> None:
    document = {"versions": {"v1": {"enabled": True}}}
    config = {"version": 1, "operations": [{"op": "update_item", "path": "$/versions", "match": {"name": "v1"}, "set": {"enabled": False}}]}
    require_raises(Exception, lambda: YamlPatchEngine().apply_document(document, config), "update_item on mapping must fail")


def test_atomic_apply_does_not_replace_output_on_failure() -> None:
    with tempfile.TemporaryDirectory() as td:
        source = Path(td) / "source.yaml"
        patch = Path(td) / "patch.yaml"
        output = Path(td) / "output.yaml"
        source.write_text("app:\n  enabled: true\n", encoding="utf-8")
        output.write_text("sentinel: keep\n", encoding="utf-8")
        patch.write_text(
            "version: 1\noptions:\n  atomic_write: true\noperations:\n"
            "  - op: replace\n    path: $/missing/path\n    value: x\n    missing: error\n",
            encoding="utf-8",
        )
        require_raises(Exception, lambda: YamlPatchEngine().apply_file(source, patch, output), "failed atomic apply must raise")
        require(output.read_text(encoding="utf-8") == "sentinel: keep\n", "failed atomic apply replaced existing output")


def test_yaml_scalar_types_remain_distinct() -> None:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    doc = yaml.load(
        "literal: |\n  line1\n  line2\n"
        "folded: >\n  one\n  two\n"
        "dateText: '2026-07-16'\n"
        "booleanText: 'false'\n"
        "numericText: '001'\n"
        "actualBool: false\n"
        "actualInt: 1\n"
    )
    config = {"version": 1, "operations": [{"op": "replace", "path": "$/actualInt", "value": 2}]}
    result = YamlPatchEngine().apply_document(doc, config)
    require(result["dateText"] == "2026-07-16" and isinstance(result["dateText"], str), "date string changed type")
    require(result["booleanText"] == "false" and isinstance(result["booleanText"], str), "boolean string changed type")
    require(result["numericText"] == "001" and isinstance(result["numericText"], str), "numeric string changed type")
    require(result["actualBool"] is False, "boolean scalar changed")
    require(type(result["actualInt"]) is int and result["actualInt"] == 2, "integer replacement wrong")
    require("line1\nline2" in str(result["literal"]), "literal block content changed")


def main() -> None:
    tests = [
        test_duplicate_yaml_key,
        test_missing_template_variable,
        test_non_unique_match_is_not_silent,
        test_selector_create_rejected,
        test_invalid_regex_rejected,
        test_out_of_range_index_rejected,
        test_wrong_container_type_rejected,
        test_atomic_apply_does_not_replace_output_on_failure,
        test_yaml_scalar_types_remain_distinct,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"PASS: enterprise YAML error matrix ({len(tests)} tests)")


if __name__ == "__main__":
    main()

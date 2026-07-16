from __future__ import annotations
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Mapping
from ruamel.yaml import YAML

DEFAULT_YAML_OUTPUT = {
    "mapping": 2,
    "sequence": 4,
    "offset": 2,
    "width": 4096,
    "preserve_quotes": True,
    "explicit_start": None,
    "explicit_end": None,
    "line_ending": "preserve",
}


def normalize_yaml_output(options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(options or {})
    # Accept either the nested options.yaml_output mapping or direct values.
    if "yaml_output" in raw:
        nested = raw.get("yaml_output") or {}
        if not isinstance(nested, Mapping):
            raise ValueError("options.yaml_output must be a mapping")
        raw = dict(nested)
    aliases = {
        "mapping_indent": "mapping",
        "sequence_indent": "sequence",
        "sequence_offset": "offset",
        "line_width": "width",
    }
    for old, new in aliases.items():
        if old in raw and new not in raw:
            raw[new] = raw[old]
    result = dict(DEFAULT_YAML_OUTPUT)
    for key in result:
        if key in raw:
            result[key] = raw[key]
    for key in ("mapping", "sequence", "offset", "width"):
        if not isinstance(result[key], int):
            raise ValueError(f"options.yaml_output.{key} must be an integer")
    if result["mapping"] < 1 or result["sequence"] < 1 or result["offset"] < 0 or result["width"] < 1:
        raise ValueError("YAML indentation and width values must be positive; offset may be zero")
    if result["offset"] >= result["sequence"]:
        raise ValueError("options.yaml_output.offset must be smaller than sequence")
    for key in ("preserve_quotes", "explicit_start", "explicit_end"):
        if result[key] is not None and not isinstance(result[key], bool):
            raise ValueError(f"options.yaml_output.{key} must be true, false, or null")
    if result["line_ending"] not in {"preserve", "lf", "crlf"}:
        raise ValueError("options.yaml_output.line_ending must be preserve, lf, or crlf")
    return result


def _render_with_line_ending(data: Iterable[Any], output_options: Mapping[str, Any] | None, multiple: bool) -> str:
    yaml = make_yaml(output_options)
    out = StringIO()
    if multiple:
        yaml.dump_all(data, out)
    else:
        yaml.dump(data, out)
    text = out.getvalue().replace("\r\n", "\n").replace("\r", "\n")
    raw = dict(output_options or {})
    detected = raw.get("_detected_line_ending", "lf")
    mode = normalize_yaml_output(output_options)["line_ending"]
    effective = detected if mode == "preserve" else mode
    return text.replace("\n", "\r\n") if effective == "crlf" else text


def make_yaml(output_options: Mapping[str, Any] | None = None) -> YAML:
    opts = normalize_yaml_output(output_options)
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = opts["preserve_quotes"]
    yaml.allow_duplicate_keys = False
    yaml.indent(mapping=opts["mapping"], sequence=opts["sequence"], offset=opts["offset"])
    yaml.width = opts["width"]
    if opts["explicit_start"] is not None:
        yaml.explicit_start = opts["explicit_start"]
    if opts["explicit_end"] is not None:
        yaml.explicit_end = opts["explicit_end"]
    return yaml


def load_one(path: str | Path) -> Any:
    yaml = make_yaml()
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.load(f)


def load_all(path: str | Path) -> list[Any]:
    yaml = make_yaml()
    with Path(path).open("r", encoding="utf-8") as f:
        return list(yaml.load_all(f))


def _encode_output(text: str, output_options: Mapping[str, Any] | None) -> bytes:
    payload = text.encode("utf-8")
    if bool(dict(output_options or {}).get("_detected_bom", False)):
        payload = b"\xef\xbb\xbf" + payload
    return payload


def dump_one(data: Any, path: str | Path, output_options: Mapping[str, Any] | None = None) -> None:
    Path(path).write_bytes(_encode_output(_render_with_line_ending(data, output_options, False), output_options))


def dump_all(data: Iterable[Any], path: str | Path, output_options: Mapping[str, Any] | None = None) -> None:
    Path(path).write_bytes(_encode_output(_render_with_line_ending(data, output_options, True), output_options))


def dumps(data: Any, output_options: Mapping[str, Any] | None = None) -> str:
    yaml = make_yaml(output_options)
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()


def _contains_yaml_merge(value: Any, seen: set[int] | None = None) -> bool:
    """Return whether a round-trip YAML object contains ``<<`` merge metadata.

    ``copy.deepcopy`` in current ruamel.yaml releases duplicates the anchor source
    and the merge reference independently. Mutating the cloned anchor then leaves
    consumers pointing at a stale copy and may materialize inherited keys on dump.
    """
    seen = seen or set()
    identity = id(value)
    if identity in seen:
        return False
    seen.add(identity)
    merge = getattr(value, 'merge', None)
    if merge:
        return True
    if isinstance(value, dict):
        return any(_contains_yaml_merge(k, seen) or _contains_yaml_merge(v, seen) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_yaml_merge(v, seen) for v in value)
    return False


def clone(data: Any) -> Any:
    """Clone YAML data while preserving anchors, aliases, comments and styles.

    The common path remains ``deepcopy``. Documents using YAML merge keys use a
    ruamel round trip because deepcopy breaks the anchor/merge object topology.
    """
    if not _contains_yaml_merge(data):
        return deepcopy(data)
    yaml = make_yaml()
    out = StringIO()
    if isinstance(data, list) and data and all(isinstance(v, dict) for v in data):
        # A list can be either a YAML sequence or the document list used by the
        # file engine. Keep it as a normal sequence here; callers cloning document
        # collections clone each document explicitly.
        yaml.dump(data, out)
    else:
        yaml.dump(data, out)
    return yaml.load(out.getvalue())

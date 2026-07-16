from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from copy import deepcopy
import os, tempfile, shutil
from typing import Any
from .yamlio import load_all, dump_all, load_one, clone
from .config_loader import load_config_with_variable_maps
from .models import EngineConfig, apply_defaults_profile
from .template import render_value
from .operations import registry, OperationContext
from .errors import ConfigError, ValidationError
from .matcher import matches
from .pathing import expand_paths, path_has_selectors
from .comparison import strict_documents_equal
from ruamel.yaml.scalarstring import PlainScalarString, SingleQuotedScalarString, DoubleQuotedScalarString




def _references_original(value: Any) -> bool:
    """Return True only when template text can read the original snapshot.

    Generated patches almost never use ``original``. Avoiding the snapshot in
    that common case removes a full-document deepcopy per replay while keeping
    exact legacy semantics whenever an original-reference is present.
    """
    if isinstance(value, str):
        return ("{{" in value or "{%" in value) and "original" in value
    if isinstance(value, dict):
        return any(_references_original(k) or _references_original(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_references_original(v) for v in value)
    return False

def _quoted_scalar(value: Any, style: str) -> Any:
    style = str(style or 'auto').lower()
    if not isinstance(value, str) or style in {'auto', 'preserve', ''}:
        return value
    if style == 'plain':
        return PlainScalarString(str(value))
    if style == 'single':
        return SingleQuotedScalarString(str(value))
    if style == 'double':
        return DoubleQuotedScalarString(str(value))
    raise ConfigError(f"Unsupported quote style: {style!r}; expected auto, preserve, plain, single, or double")


def _set_relative_style(root: Any, dotted: str, style: str) -> None:
    parts = [p for p in str(dotted).split('.') if p != '']
    if not parts:
        return
    current = root
    for token in parts[:-1]:
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current[token]
    last = parts[-1]
    if isinstance(current, list):
        idx = int(last); current[idx] = _quoted_scalar(current[idx], style)
    else:
        current[last] = _quoted_scalar(current[last], style)


def _apply_quote_directives(spec: dict[str, Any]) -> dict[str, Any]:
    quote = spec.get('quote')
    if quote is not None:
        for field in ('value', 'replacement', 'new'):
            if field in spec:
                spec[field] = _quoted_scalar(spec[field], str(quote))
                break
    styles = spec.get('quote_styles')
    if styles is not None:
        if not isinstance(styles, dict):
            raise ConfigError('quote_styles must be a mapping of payload paths to styles')
        for dotted, style in styles.items():
            parts = str(dotted).split('.', 1)
            if len(parts) == 1:
                field, relative = 'value', parts[0]
            else:
                field, relative = parts
            if field not in spec:
                raise ConfigError(f'quote_styles references missing field: {field}')
            _set_relative_style(spec[field], relative, str(style))
    return spec

@dataclass
class ApplyResult:
    input_path: Path
    output_path: Path | None
    changed: bool
    documents: list[Any]
    applied_operations: list[str] = field(default_factory=list)
    skipped_operations: list[dict[str, Any]] = field(default_factory=list)

class YamlPatchEngine:
    def load_config(self, path: str | Path, variable_map_files: list[str | Path] | None = None) -> EngineConfig:
        return EngineConfig.model_validate(load_config_with_variable_maps(path, variable_map_files))

    def apply_document(self, document: Any, config: EngineConfig | dict[str, Any], extra_variables: dict[str, Any] | None = None, *, track_no_effect: bool = True) -> Any:
        cfg = config if isinstance(config, EngineConfig) else EngineConfig.model_validate(config)
        operations_source = config.operations if isinstance(config, EngineConfig) else config.get("operations", [])
        original = clone(document) if _references_original(operations_source) else document
        skipped: list[dict[str, Any]] = []
        ctx = OperationContext(document=document, original=original, variables={}, captures={}, skipped_operations=skipped)
        variables = dict(cfg.variables)
        variables.update(extra_variables or {})
        for raw in cfg.operations:
            operation_before = clone(ctx.document) if track_no_effect else None
            skipped_before = len(skipped)
            context = {**variables, "captures": ctx.captures, **ctx.captures, "original": original, "current": ctx.document}
            spec = apply_defaults_profile(_apply_quote_directives(render_value(raw, context)), cfg.defaults_profile)
            if "op" not in spec: raise ConfigError("Operation missing op")
            raw_paths = spec.get("paths")
            if raw_paths is not None:
                patterns = list(raw_paths)
                concrete_paths: list[str] = []
                seen_paths: set[str] = set()
                for pattern in patterns:
                    matches = expand_paths(ctx.document, pattern) if path_has_selectors(pattern) else [pattern]
                    if not matches:
                        policy = str(spec.get("missing", "skip")).lower()
                        if policy == "skip" or str(spec.get("on_zero_matches", "")).lower() in {"skip", "ignore"}:
                            skipped.append({"id": spec.get("id"), "op": spec.get("op"), "path": pattern, "reason": "paths entry matched no nodes"})
                            continue
                        if policy == "create" or spec.get("create_missing") is True:
                            raise ConfigError("missing: create is not supported for an unmatched selector in paths")
                        raise ConfigError(f"paths entry matched no nodes: {pattern!r}")
                    for matched in matches:
                        if matched not in seen_paths:
                            seen_paths.add(matched)
                            concrete_paths.append(matched)
                if spec["op"] == "remove":
                    concrete_paths = list(reversed(concrete_paths))
                for concrete_path in concrete_paths:
                    expanded = deepcopy(spec)
                    expanded.pop("paths", None)
                    expanded["path"] = concrete_path
                    registry.execute(expanded["op"], ctx, expanded)
            else:
                path = spec.get("path")
                if isinstance(path, str) and path_has_selectors(path):
                    concrete_paths = expand_paths(ctx.document, path)
                    if not concrete_paths:
                        policy = str(spec.get("missing", "skip")).lower()
                        if policy == "skip" or str(spec.get("on_zero_matches", "")).lower() in {"skip", "ignore"}:
                            skipped.append({"id": spec.get("id"), "op": spec.get("op"), "path": path, "reason": "selector path matched no nodes"})
                            continue
                        if policy == "create" or spec.get("create_missing") is True:
                            raise ConfigError("missing: create is not supported for selector paths")
                        raise ConfigError(f"Selector path matched no nodes: {path!r}")
                    if spec["op"] == "remove":
                        concrete_paths = list(reversed(concrete_paths))
                    for concrete_path in concrete_paths:
                        expanded = deepcopy(spec)
                        expanded["path"] = concrete_path
                        registry.execute(expanded["op"], ctx, expanded)
                else:
                    registry.execute(spec["op"], ctx, spec)
            if track_no_effect and len(skipped) == skipped_before and strict_documents_equal([ctx.document], [operation_before]):
                skipped.append({
                    "id": spec.get("id"), "op": spec.get("op"), "path": spec.get("path"),
                    "reason": "no effect (target missing, selector matched nothing, or value already satisfied)",
                })
        self.last_skipped_operations = skipped
        return ctx.document

    def apply_file(self, source: str | Path, config: EngineConfig | dict[str, Any] | str | Path,
                   output: str | Path | None = None, variables: dict[str, Any] | None = None,
                   document_index: int | None = None, dry_run: bool | None = None,
                   variable_map_files: list[str | Path] | None = None) -> ApplyResult:
        source = Path(source)
        cfg = self.load_config(config, variable_map_files) if isinstance(config, (str, Path)) else (config if isinstance(config, EngineConfig) else EngineConfig.model_validate(config))
        source_bytes = source.read_bytes()
        detected_line_ending = "crlf" if b"\r\n" in source_bytes else "lf"
        detected_bom = source_bytes.startswith(b"\xef\xbb\xbf")
        docs = load_all(source)
        before = deepcopy(docs)
        if getattr(cfg, 'folder_action', None) == 'replace_all_documents' or (cfg.model_extra or {}).get('folder_action') == 'replace_all_documents':
            docs = deepcopy(cfg.operations[0]['value'])
            indices = []
        else:
            indices = None
        if indices is not None:
            pass
        elif document_index is not None:
            indices = [document_index]
        elif cfg.documents:
            selectors = cfg.documents if isinstance(cfg.documents, list) else [cfg.documents]
            indices = [i for i, doc in enumerate(docs) if any(matches(doc, sel.get("match", sel)) for sel in selectors)]
            if not indices and cfg.options.get("on_no_document_match", "error") == "error":
                raise ValidationError("No YAML document matched config.documents")
        else:
            indices = list(range(len(docs)))
        skipped_operations: list[dict[str, Any]] = []
        for idx in indices:
            docs[idx] = self.apply_document(docs[idx], cfg, variables)
            skipped_operations.extend(getattr(self, "last_skipped_operations", []))
        changed = not strict_documents_equal(docs, before)
        effective_dry_run = cfg.options.get("dry_run", False) if dry_run is None else dry_run
        output_path = Path(output) if output else source
        if not effective_dry_run and changed:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if cfg.options.get("backup", False) and output_path.exists():
                shutil.copy2(output_path, output_path.with_suffix(output_path.suffix + ".bak"))
            if cfg.options.get("atomic_write", True):
                fd, tmp = tempfile.mkstemp(prefix=output_path.name, dir=str(output_path.parent))
                os.close(fd)
                try:
                    output_options = dict(cfg.options)
                    output_options["_detected_line_ending"] = detected_line_ending
                    output_options["_detected_bom"] = detected_bom
                    dump_all(docs, tmp, output_options)
                    os.replace(tmp, output_path)
                finally:
                    if os.path.exists(tmp): os.unlink(tmp)
            else:
                output_options = dict(cfg.options)
                output_options["_detected_line_ending"] = detected_line_ending
                dump_all(docs, output_path, output_options)
            # syntax re-read validation
            try: load_all(output_path)
            except Exception as e: raise ValidationError(f"Written YAML cannot be parsed: {e}") from e
        return ApplyResult(source, None if effective_dry_run else output_path, changed, docs,
                           [op.get("id", op.get("op", "unknown")) for op in cfg.operations], skipped_operations)

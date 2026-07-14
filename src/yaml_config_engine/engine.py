from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from copy import deepcopy
import os, tempfile, shutil
from typing import Any
from .yamlio import load_all, dump_all, load_one, clone
from .config_loader import load_config_with_variable_maps
from .models import EngineConfig
from .template import render_value
from .operations import registry, OperationContext
from .errors import ConfigError, ValidationError
from .matcher import matches
from .pathing import expand_paths
from .comparison import strict_documents_equal

@dataclass
class ApplyResult:
    input_path: Path
    output_path: Path | None
    changed: bool
    documents: list[Any]
    applied_operations: list[str] = field(default_factory=list)

class YamlPatchEngine:
    def load_config(self, path: str | Path) -> EngineConfig:
        return EngineConfig.model_validate(load_config_with_variable_maps(path))

    def apply_document(self, document: Any, config: EngineConfig | dict[str, Any], extra_variables: dict[str, Any] | None = None) -> Any:
        cfg = config if isinstance(config, EngineConfig) else EngineConfig.model_validate(config)
        original = clone(document)
        ctx = OperationContext(document=document, original=original, variables={}, captures={})
        variables = dict(cfg.variables)
        variables.update(extra_variables or {})
        for raw in cfg.operations:
            context = {**variables, "captures": ctx.captures, **ctx.captures, "original": original, "current": ctx.document}
            spec = render_value(deepcopy(raw), context)
            if "op" not in spec: raise ConfigError("Operation missing op")
            path = spec.get("path")
            if isinstance(path, str) and ("*" in path):
                concrete_paths = expand_paths(ctx.document, path)
                if not concrete_paths:
                    policy = str(spec.get("missing", "error")).lower()
                    if policy == "skip" or str(spec.get("on_zero_matches", "")).lower() in {"skip", "ignore"}:
                        continue
                    if policy == "create" or spec.get("create_missing") is True:
                        raise ConfigError("missing: create is not supported for wildcard paths")
                    raise ConfigError(f"Wildcard path matched no nodes: {path!r}")
                # Removing list items must run from highest index to lowest so
                # earlier removals do not shift later concrete indices.
                if spec["op"] == "remove":
                    concrete_paths = list(reversed(concrete_paths))
                for concrete_path in concrete_paths:
                    expanded = deepcopy(spec)
                    expanded["path"] = concrete_path
                    registry.execute(expanded["op"], ctx, expanded)
            else:
                registry.execute(spec["op"], ctx, spec)
        return ctx.document

    def apply_file(self, source: str | Path, config: EngineConfig | dict[str, Any] | str | Path,
                   output: str | Path | None = None, variables: dict[str, Any] | None = None,
                   document_index: int | None = None, dry_run: bool | None = None) -> ApplyResult:
        source = Path(source)
        cfg = self.load_config(config) if isinstance(config, (str, Path)) else (config if isinstance(config, EngineConfig) else EngineConfig.model_validate(config))
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
        for idx in indices:
            docs[idx] = self.apply_document(docs[idx], cfg, variables)
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
                           [op.get("id", op.get("op", "unknown")) for op in cfg.operations])

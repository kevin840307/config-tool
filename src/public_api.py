from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Literal
import shutil

from yaml_config_engine.engine import YamlPatchEngine
from yaml_config_engine.diff_compiler import DiffCompiler
from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.yamlio import load_all, load_one, dump_one
from yaml_config_engine.comparison import strict_documents_equal
from yaml_config_engine.mapping_generalizer import verified_generalize_config
from yaml_config_engine.variable_scope import resolve_scope_variables
from yaml_config_engine.config_loader import _merge_maps, _normalize_map_document
from xml_config_engine.engine import XmlPatchEngine
from xml_config_engine.compiler import XmlDiffCompiler
from xml_config_engine.folder_compiler import XmlFolderCompiler
from mixed_folder import MixedFolderCompiler

Format = Literal['auto', 'yaml', 'xml', 'mixed']

@dataclass
class ConfigToolResult:
    ok: bool
    action: str
    format: str
    output: Path | None = None
    changed: bool | None = None
    verified: bool | None = None
    strategy: str | None = None
    warnings: list[str] = field(default_factory=list)
    skipped_operations: list[dict[str, Any]] = field(default_factory=list)
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Path): return str(value)
            if is_dataclass(value): return {k: convert(v) for k, v in asdict(value).items()}
            if isinstance(value, dict): return {k: convert(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)): return [convert(v) for v in value]
            return value
        return convert(asdict(self))

class ConfigTool:
    """Stable Python facade for YAML/XML compile, apply, verify and folder workflows."""

    def __init__(self, *, retry_protection: bool = False, readable: bool = True) -> None:
        self.retry_protection = retry_protection
        self.readable = readable

    @staticmethod
    def _detect(path: str | Path, requested: Format = 'auto') -> str:
        if requested != 'auto': return requested
        suffix = Path(path).suffix.lower()
        if suffix in {'.yaml', '.yml'}: return 'yaml'
        if suffix == '.xml': return 'xml'
        raise ValueError(f'Cannot detect format from {path!s}; pass format="yaml" or format="xml"')

    @staticmethod
    def _normalize_mapping_files(files: str | Path | Iterable[str | Path] | None) -> list[str | Path]:
        if files is None:
            return []
        if isinstance(files, (str, Path)):
            return [files]
        return list(files)

    @staticmethod
    def _ensure_parent(path: str | Path | None) -> None:
        if path is not None:
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolved_mapping_variables(files: list[str | Path], fab: str, env: str) -> tuple[dict[str, Any], list[str]]:
        merged: dict[str, Any] = {}
        for ref in files:
            path = Path(ref).expanduser().resolve()
            merged = _merge_maps(merged, _normalize_map_document(load_one(path), path))
        return resolve_scope_variables(merged, fab, env)

    def compile(self, before: str | Path, after: str | Path, output: str | Path, *,
                format: Format = 'auto', identity_keys: list[str] | None = None,
                retry_protection: bool | None = None, readable: bool | None = None,
                variable_map_files: str | Path | Iterable[str | Path] | None = None,
                fab: str = '', env: str = '') -> ConfigToolResult:
        fmt = self._detect(before, format)
        after_fmt = self._detect(after, 'auto') if format == 'auto' else fmt
        if after_fmt != fmt:
            raise ValueError(f'Before/after format mismatch: {fmt} vs {after_fmt}')
        output_path = Path(output)
        self._ensure_parent(output_path)
        warnings: list[str] = []
        if fmt == 'yaml':
            before_docs, after_docs = load_all(before), load_all(after)
            if len(before_docs) == len(after_docs) == 1:
                compiler = DiffCompiler(identity_keys, retry_protection=self.retry_protection if retry_protection is None else retry_protection,
                                        readable=self.readable if readable is None else readable)
                result = compiler.compile(before_docs[0], after_docs[0])
                config, verified, strategy = result.config, result.verified, result.strategy
                warnings.extend(result.warnings)
                generalized = False
                refs = self._normalize_mapping_files(variable_map_files)
                if refs:
                    resolved, scopes = self._resolved_mapping_variables(refs, fab, env)
                    if resolved:
                        candidate, accepted = verified_generalize_config(before_docs[0], after_docs[0], config, resolved)
                        if accepted:
                            config = candidate
                            stored = []
                            for ref in refs:
                                rp = Path(ref).expanduser().resolve()
                                try: stored.append(str(rp.relative_to(output_path.resolve().parent)))
                                except ValueError: stored.append(str(rp))
                            config['variable_map_file'] = stored[0] if len(stored) == 1 else stored
                            config['scope'] = {'fab': fab, 'env': env}
                            generalized = True
                            warnings.append('Generalized generated values with mapping scopes: ' + ', '.join(scopes))
                        else:
                            warnings.append('Mapping generalization failed strict replay and was rolled back.')
                    else:
                        warnings.append('No mapping variables matched the selected FAB/ENV scope.')
                dump_one(config, output_path)
                return ConfigToolResult(bool(verified), 'compile', fmt, output_path, verified=bool(verified), strategy=strategy,
                                        warnings=warnings, data={'config': config, 'generalized': generalized})
            config = {'version': 1, 'options': {'atomic_write': True}, 'folder_action': 'replace_all_documents',
                      'document_mode': 'all', 'operations': [{'id': 'replace-documents', 'op': 'replace', 'path': '$', 'value': after_docs}]}
            dump_one(config, output_path)
            return ConfigToolResult(True, 'compile', fmt, output_path, verified=True, strategy='replace-all-documents',
                                    warnings=['Multi-document input used strict replace-all-documents.'], data={'config': config})
        if fmt == 'xml':
            result = XmlDiffCompiler().compile_files(before, after)
            dump_one(result.config, output_path)
            return ConfigToolResult(bool(result.verified), 'compile', fmt, output_path, verified=bool(result.verified),
                                    strategy=result.strategy, warnings=list(result.warnings), data={'config': result.config})
        raise ValueError('compile supports yaml or xml; use compile_folder for mixed folders')

    def apply(self, source: str | Path, config: str | Path | dict[str, Any], output: str | Path | None = None, *,
              format: Format = 'auto', variables: dict[str, Any] | None = None,
              variable_map_files: str | Path | Iterable[str | Path] | None = None, dry_run: bool = False,
              document_index: int | None = None) -> ConfigToolResult:
        fmt = self._detect(source, format)
        self._ensure_parent(output)
        mapping_files = self._normalize_mapping_files(variable_map_files)
        if fmt == 'yaml':
            r = YamlPatchEngine().apply_file(source, config, output, variables, document_index, dry_run, mapping_files)
        elif fmt == 'xml':
            r = XmlPatchEngine().apply_file(source, config, output, variables, dry_run, mapping_files)
        else:
            raise ValueError('apply supports yaml or xml; use apply_folder for mixed folders')
        return ConfigToolResult(True, 'apply', fmt, r.output_path, changed=r.changed,
                                skipped_operations=list(getattr(r, 'skipped_operations', [])), data=r)

    def verify(self, before: str | Path, config: str | Path | dict[str, Any], expected: str | Path, *,
               format: Format = 'auto', variables: dict[str, Any] | None = None,
               variable_map_files: str | Path | Iterable[str | Path] | None = None, exact_bytes: bool = False) -> ConfigToolResult:
        fmt = self._detect(before, format)
        expected_fmt = self._detect(expected, 'auto') if format == 'auto' else fmt
        if expected_fmt != fmt:
            raise ValueError(f'Before/expected format mismatch: {fmt} vs {expected_fmt}')
        mapping_files = self._normalize_mapping_files(variable_map_files)
        with TemporaryDirectory(prefix='config-tool-api-verify-') as tmp:
            actual = Path(tmp) / Path(before).name
            shutil.copy2(before, actual)
            self.apply(actual, config, actual, format=fmt, variables=variables, variable_map_files=mapping_files)
            if exact_bytes:
                ok = actual.read_bytes() == Path(expected).read_bytes()
            elif fmt == 'yaml':
                ok = strict_documents_equal(load_all(actual), load_all(expected))
            else:
                ok = XmlDiffCompiler._structural_equal(actual.read_text(encoding='utf-8-sig'), Path(expected).read_text(encoding='utf-8-sig'))
        return ConfigToolResult(ok, 'verify', fmt, verified=ok, data={'exact_bytes': exact_bytes})

    def compile_folder(self, before_root: str | Path, after_root: str | Path, output_root: str | Path, *,
                       format: Format = 'mixed', include_unchanged: bool = False, verify: bool = True,
                       layout: str = 'compact', matched_files_only: bool = False, exact_bytes: bool = False,
                       **kwargs: Any) -> ConfigToolResult:
        Path(output_root).mkdir(parents=True, exist_ok=True)
        if format in {'auto', 'mixed'}:
            r = MixedFolderCompiler().compile_folder(before_root, after_root, output_root, include_unchanged=include_unchanged,
                    verify=verify, layout=layout, matched_files_only=matched_files_only, exact_bytes=exact_bytes)
            return ConfigToolResult(bool(r.get('verified', True)), 'compile_folder', 'mixed', Path(output_root), verified=r.get('verified'), data=r)
        if format == 'yaml':
            r = FolderCompiler(retry_protection=self.retry_protection, readable=self.readable).compile_folder(
                before_root, after_root, output_root, include_unchanged=include_unchanged, verify=verify, layout=layout,
                matched_files_only=matched_files_only, exact_bytes=exact_bytes, **kwargs)
        elif format == 'xml':
            r = XmlFolderCompiler().compile_folder(before_root, after_root, output_root, include_unchanged, verify, layout, matched_files_only)
        else: raise ValueError(format)
        return ConfigToolResult(bool(r.verified), 'compile_folder', format, Path(output_root), verified=bool(r.verified), data=r)

    def apply_folder(self, source_root: str | Path, generated_root: str | Path, output_root: str | Path, *,
                     format: Format = 'mixed', variables: dict[str, Any] | None = None,
                     variable_map_files: str | Path | Iterable[str | Path] | None = None) -> ConfigToolResult:
        Path(output_root).mkdir(parents=True, exist_ok=True)
        mapping_files = self._normalize_mapping_files(variable_map_files)
        if format in {'auto', 'mixed'}:
            r = MixedFolderCompiler().apply_folder(source_root, generated_root, output_root, variables, mapping_files)
        elif format == 'yaml':
            r = FolderCompiler().apply_manifest(source_root, generated_root, output_root, variables=variables, variable_map_files=mapping_files)
        elif format == 'xml':
            r = XmlFolderCompiler().apply_folder(source_root, generated_root, output_root, variables, mapping_files)
        else: raise ValueError(format)
        return ConfigToolResult(True, 'apply_folder', 'mixed' if format == 'auto' else format, Path(output_root), data=r)

    def verify_folder(self, source_root: str | Path, generated_root: str | Path, expected_root: str | Path, *,
                      format: Format = 'mixed', exact_bytes: bool = False,
                      variables: dict[str, Any] | None = None,
                      variable_map_files: str | Path | Iterable[str | Path] | None = None) -> ConfigToolResult:
        """Apply the generated folder patch in an isolated directory and compare it with expected_root.

        Runtime variables and mapping files are accepted exactly as in apply_folder, keeping the
        public Python API contract aligned across apply and verify workflows.
        """
        with TemporaryDirectory(prefix='config-tool-folder-verify-') as tmp:
            actual = Path(tmp) / 'actual'
            self.apply_folder(source_root, generated_root, actual, format=format,
                              variables=variables, variable_map_files=variable_map_files)
            ok = MixedFolderCompiler()._trees_equal(actual, Path(expected_root), exact_bytes=exact_bytes)
        return ConfigToolResult(bool(ok), 'verify_folder', 'mixed' if format == 'auto' else format,
                                verified=bool(ok), data={'exact_bytes': exact_bytes})

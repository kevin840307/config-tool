from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import tempfile
from typing import Any
import fnmatch
import shutil
import os
import base64

from .diff_compiler import DiffCompiler
from .engine import YamlPatchEngine
from .models import EngineConfig
from .yamlio import load_all, load_one, dump_one, dump_all
from .comparison import strict_documents_equal
from .variable_scope import resolve_scope_variables
from folder_path_resolver import resolve_file_keys
from file_key_generalizer import generalize_file_map, load_compile_path_context
from .config_loader import load_config_with_variable_maps
from .mapping_generalizer import verified_generalize_config, generalize_operations

YAML_SUFFIXES = {'.yaml', '.yml'}


@dataclass
class FolderEntry:
    relative_path: str
    action: str
    config: str | None = None
    strategy: str | None = None
    verified: bool = False
    warnings: list[str] | None = None


@dataclass
class FolderCompileResult:
    manifest_path: Path
    entries: list[FolderEntry]
    verified: bool
    compact_path: Path | None = None


class FolderCompiler:
    def __init__(self, *, retry_protection: bool = False, readable: bool = True) -> None:
        self.compiler = DiffCompiler(retry_protection=retry_protection, readable=readable)
        self.engine = YamlPatchEngine()

    @staticmethod
    def _matches(path: Path, include: list[str], exclude: list[str]) -> bool:
        posix = path.as_posix()
        included = any(fnmatch.fnmatch(posix, pattern) for pattern in include)
        excluded = any(fnmatch.fnmatch(posix, pattern) for pattern in exclude)
        return included and not excluded

    @staticmethod
    def _path_allowed(rel: Path, *, path_allow: list[str], path_deny: list[str],
                      fab_allow_prefix: list[str], fab_deny_prefix: list[str],
                      env_allow: list[str], env_deny: list[str]) -> bool:
        posix = rel.as_posix()
        parts = rel.parts
        fab = parts[0] if len(parts) >= 3 else ''
        env = parts[1] if len(parts) >= 3 else ''
        app_rel = Path(*parts[2:]).as_posix() if len(parts) >= 3 else posix
        def match_any(patterns: list[str]) -> bool:
            return any(fnmatch.fnmatch(posix, p) or fnmatch.fnmatch(app_rel, p) or posix == p or app_rel == p for p in patterns)
        if path_allow and not match_any(path_allow): return False
        if path_deny and match_any(path_deny): return False
        if fab_allow_prefix and not any(fab.startswith(x) for x in fab_allow_prefix): return False
        if fab_deny_prefix and any(fab.startswith(x) for x in fab_deny_prefix): return False
        if env_allow and env not in env_allow: return False
        if env_deny and env in env_deny: return False
        return True

    def _collect(self, root: Path, include: list[str], exclude: list[str], *,
                 path_allow: list[str] | None = None, path_deny: list[str] | None = None,
                 fab_allow_prefix: list[str] | None = None, fab_deny_prefix: list[str] | None = None,
                 env_allow: list[str] | None = None, env_deny: list[str] | None = None) -> dict[str, Path]:
        result: dict[str, Path] = {}
        if not root.exists():
            return result
        for file in root.rglob('*'):
            if not file.is_file() or file.suffix.lower() not in YAML_SUFFIXES:
                continue
            rel = file.relative_to(root)
            if self._matches(rel, include, exclude) and self._path_allowed(
                rel, path_allow=path_allow or [], path_deny=path_deny or [],
                fab_allow_prefix=fab_allow_prefix or [], fab_deny_prefix=fab_deny_prefix or [],
                env_allow=env_allow or [], env_deny=env_deny or []):
                result[rel.as_posix()] = file
        return result

    @staticmethod
    def _config_rel(relative_path: str) -> str:
        p = Path(relative_path)
        return (Path('configs') / p.parent / f'{p.name}.patch.yaml').as_posix()

    @staticmethod
    def _compact_operation(op: dict[str, Any]) -> Any:
        """Use readable shorthand for common operations; keep advanced operations lossless."""
        name = op.get('op')
        if name in {'set', 'replace'} and set(op).issubset({'id', 'op', 'path', 'value', 'create_missing'}):
            value = [op.get('path', '$'), op.get('value')]
            if op.get('create_missing') is not None:
                return {name: {'path': op.get('path', '$'), 'value': op.get('value'), 'create_missing': op['create_missing']}}
            return {name: value}
        if name == 'remove' and set(op).issubset({'id', 'op', 'path', 'ignore_missing'}):
            if op.get('ignore_missing') is not None:
                return {'remove': {'path': op.get('path', '$'), 'ignore_missing': op['ignore_missing']}}
            return {'remove': op.get('path', '$')}
        return {k: v for k, v in op.items() if k != 'id'}

    @staticmethod
    def _expand_compact_operation(raw: Any, index: int) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError(f'Invalid compact operation at index {index}: expected mapping')
        if 'op' in raw:
            return dict(raw)
        if len(raw) != 1:
            raise ValueError(f'Invalid compact operation at index {index}: expected one operation key')
        name, value = next(iter(raw.items()))
        if name in {'set', 'replace'}:
            if isinstance(value, list) and len(value) == 2:
                return {'id': f'compact-{index}', 'op': name, 'path': value[0], 'value': value[1]}
            if isinstance(value, dict):
                return {'id': f'compact-{index}', 'op': name, **value}
        if name == 'remove':
            if isinstance(value, str):
                return {'id': f'compact-{index}', 'op': 'remove', 'path': value}
            if isinstance(value, dict):
                return {'id': f'compact-{index}', 'op': 'remove', **value}
        if isinstance(value, dict):
            return {'id': f'compact-{index}', 'op': name, **value}
        raise ValueError(f'Unsupported compact operation at index {index}: {name}')

    def _write_compact_patch(self, output_root: Path, entries: list[FolderEntry], *, source_root: Path, path_variables: dict[str, Any] | None = None, variable_map: dict[str, dict[str, Any]] | None = None) -> Path:
        files: dict[str, Any] = {}
        for entry in entries:
            if entry.action == 'unchanged':
                continue
            if entry.action == 'delete':
                files[entry.relative_path] = {'delete': True}
                continue
            if not entry.config:
                continue
            cfg = load_one(output_root / entry.config)
            operations = cfg.get('operations', [])
            if entry.action == 'create':
                item = {'strategy': entry.strategy}
                if cfg.get('yaml_exact_bytes_base64'):
                    item['create_bytes_base64'] = cfg['yaml_exact_bytes_base64']
                else:
                    item['create_documents'] = operations[0].get('value', []) if operations else []
                files[entry.relative_path] = item
            elif cfg.get('folder_action') in {'replace_all_documents', 'replace_exact_bytes'}:
                item = {'strategy': entry.strategy}
                if cfg.get('yaml_exact_bytes_base64'):
                    item['replace_bytes_base64'] = cfg['yaml_exact_bytes_base64']
                else:
                    item['replace_documents'] = operations[0].get('value', []) if operations else []
                files[entry.relative_path] = item
            else:
                # Compact output is assembled from the exact per-file config
                # generated by the single-file compiler. Do not normalize the
                # operations a second time, because that can discard ruamel
                # metadata or subtly change operation semantics.
                item: dict[str, Any] = {'config': cfg}
                if entry.strategy:
                    item['strategy'] = entry.strategy
                files[entry.relative_path] = item
        files, generalization = generalize_file_map(files, source_root, variables=path_variables)
        compact = {
            'version': 1,
            'kind': 'yaml-folder-patch-compact',
            'files': files,
            'summary': {
                action: sum(1 for x in entries if x.action == action)
                for action in ('patch', 'create', 'delete', 'unchanged')
            },
        }
        path = output_root / 'patch.yaml'
        dump_one(compact, path)
        return path

    def apply_compact(
        self,
        source_root: str | Path,
        patch_path: str | Path,
        output_root: str | Path,
        *,
        variables: dict[str, Any] | None = None,
        variable_map_files: list[str | Path] | None = None,
    ) -> None:
        source_root = Path(source_root).resolve()
        patch_path = Path(patch_path).resolve()
        output_root = Path(output_root).resolve()
        patch = load_config_with_variable_maps(patch_path, variable_map_files)
        if patch.get('kind') != 'yaml-folder-patch-compact':
            raise ValueError(f'Not a compact folder patch: {patch_path}')
        if output_root.exists():
            shutil.rmtree(output_root)
        shutil.copytree(source_root, output_root)
        claimed_targets: dict[str, str] = {}
        for raw_rel_text, spec in (patch.get('files') or {}).items():
            rel_targets = resolve_file_keys(output_root, str(raw_rel_text), patch, variables)
            for rel_text in rel_targets:
                previous = claimed_targets.get(rel_text)
                if previous is not None:
                    raise ValueError(f'Folder patch file patterns overlap at {rel_text}: {previous!r} and {raw_rel_text!r}')
                claimed_targets[rel_text] = str(raw_rel_text)
                target = output_root / Path(rel_text)
                if spec.get('delete') is True:
                    if target.exists():
                        target.unlink()
                    continue
                if 'create_bytes_base64' in spec or 'replace_bytes_base64' in spec:
                    encoded = spec.get('create_bytes_base64', spec.get('replace_bytes_base64'))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(base64.b64decode(encoded))
                    continue
                if 'create_documents' in spec or 'replace_documents' in spec:
                    docs = spec.get('create_documents', spec.get('replace_documents'))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    dump_all(docs, target)
                    continue
                parts = Path(rel_text).parts
                fab = parts[0] if len(parts) >= 3 else ''
                env = parts[1] if len(parts) >= 3 else ''
                scope_vars, _ = resolve_scope_variables(patch.get('variable_map', {}), fab, env)
                merged_vars = dict(patch.get('variables') or {})
                merged_vars.update(scope_vars)
                merged_vars.update(variables or {})
                if 'config' in spec:
                    cfg = dict(spec.get('config') or {})
                else:
                    # Backward compatibility for compact patches produced before
                    # per-file config composition was introduced.
                    operations = [self._expand_compact_operation(op, i) for i, op in enumerate(spec.get('ops', []), 1)]
                    cfg = {
                        'version': 1,
                        'options': patch.get('options') or {'atomic_write': True},
                        'variables': patch.get('variables') or {},
                        'variable_map': patch.get('variable_map') or {},
                        'operations': operations,
                    }
                self.engine.apply_file(target, cfg, target, merged_vars)

    def compile_folder(
        self,
        before_root: str | Path,
        after_root: str | Path,
        output_root: str | Path,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        include_unchanged: bool = False,
        verify: bool = True,
        path_allow: list[str] | None = None, path_deny: list[str] | None = None,
        fab_allow_prefix: list[str] | None = None, fab_deny_prefix: list[str] | None = None,
        env_allow: list[str] | None = None, env_deny: list[str] | None = None,
        layout: str = 'compact',
        matched_files_only: bool = False,
        exact_bytes: bool = False,
        variables: dict[str, Any] | None = None,
        variable_map_files: list[str | Path] | None = None,
        fab: str = '',
        env: str = '',
    ) -> FolderCompileResult:
        before_root = Path(before_root).resolve()
        path_variables, compiled_variable_map = load_compile_path_context(variable_map_files, variables, fab, env)
        after_root = Path(after_root).resolve()
        output_root = Path(output_root).resolve()
        include = include or ['**/*.yaml', '**/*.yml', '*.yaml', '*.yml']
        exclude = exclude or ['**/.git/**', '**/.vs/**', '**/__pycache__/**', '**/backup/**']
        if layout not in {'compact', 'expanded'}:
            raise ValueError("layout must be 'compact' or 'expanded'")
        if output_root.exists():
            shutil.rmtree(output_root)
        output_root.mkdir(parents=True, exist_ok=True)

        collect_kwargs = dict(path_allow=path_allow, path_deny=path_deny,
            fab_allow_prefix=fab_allow_prefix, fab_deny_prefix=fab_deny_prefix,
            env_allow=env_allow, env_deny=env_deny)
        before = self._collect(before_root, include, exclude, **collect_kwargs)
        after = self._collect(after_root, include, exclude, **collect_kwargs)
        entries: list[FolderEntry] = []

        for rel in sorted(set(before) | set(after)):
            a = before.get(rel)
            b = after.get(rel)
            if matched_files_only and (a is None or b is None):
                continue
            if a is None and b is not None:
                cfg_rel = self._config_rel(rel)
                cfg = {
                    'version': 1,
                    'options': {'atomic_write': True},
                    'operations': [{'id': 'create-document', 'op': 'replace', 'path': '$', 'value': load_all(b)}],
                    'folder_action': 'create_file',
                    'document_mode': 'all',
                }
                if path_variables:
                    cfg['operations'] = generalize_operations(cfg['operations'], path_variables)
                if exact_bytes:
                    cfg['yaml_exact_bytes_base64'] = base64.b64encode(b.read_bytes()).decode('ascii')
                cfg_path = output_root / cfg_rel
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                dump_one(cfg, cfg_path)
                entries.append(FolderEntry(rel, 'create', cfg_rel, 'create-file', True, []))
                continue
            if a is not None and b is None:
                entries.append(FolderEntry(rel, 'delete', None, 'delete-file', True, []))
                continue

            assert a is not None and b is not None
            a_docs, b_docs = load_all(a), load_all(b)
            if strict_documents_equal(a_docs, b_docs):
                if include_unchanged:
                    entries.append(FolderEntry(rel, 'unchanged', None, 'no-change', True, []))
                continue

            cfg_rel = self._config_rel(rel)
            cfg_path = output_root / cfg_rel
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            if len(a_docs) == 1 and len(b_docs) == 1:
                compiled = self.compiler.compile(a_docs[0], b_docs[0])
                cfg = compiled.config
                strategy = compiled.strategy
                verified_one = compiled.verified
                warnings = list(compiled.warnings)
                # Folder auto-compile must generalize content values too, not
                # only file keys. Mapping values remain external; patch.yaml
                # stores templates such as {{ ver }} without embedding values.
                if path_variables:
                    generalized_cfg, accepted = verified_generalize_config(a_docs[0], b_docs[0], cfg, path_variables)
                    if accepted:
                        cfg = generalized_cfg
                        cfg.pop('variables', None)
                        cfg.pop('variable_map', None)
                        cfg.pop('variable_map_file', None)
                        cfg.pop('scope', None)
                    else:
                        warnings.append('Variable mapping generalization failed strict replay and was rolled back.')
                # Folder compilation must preserve the single-file compiler result.
                # Byte-for-byte equality is optional because comments, quote style,
                # trailing newlines, or harmless formatting differences must not
                # silently replace a readable config with Base64.
                with TemporaryDirectory(prefix='yaml-file-verify-') as tmp:
                    candidate = Path(tmp) / a.name
                    shutil.copy2(a, candidate)
                    try:
                        self.engine.apply_file(candidate, cfg, candidate, path_variables or {})
                        exact_match = candidate.read_bytes() == b.read_bytes()
                    except Exception as exc:
                        exact_match = False
                        warnings.append(f'Generated operations could not be file-verified: {exc}')
                if not exact_match:
                    if exact_bytes:
                        cfg = {
                            'version': 1,
                            'options': {'atomic_write': True},
                            'operations': [],
                            'folder_action': 'replace_exact_bytes',
                            'yaml_exact_bytes_base64': base64.b64encode(b.read_bytes()).decode('ascii'),
                        }
                        strategy = 'replace-entire-file-exact'
                        verified_one = True
                        warnings.append(
                            'Generated operations did not reproduce the target byte-for-byte; exact-byte fallback used because exact_bytes is enabled.'
                        )
                    else:
                        warnings.append(
                            'Generated operations are structurally equivalent but not byte-identical; readable single-file config was retained.'
                        )
            else:
                cfg = {
                    'version': 1,
                    'options': {'atomic_write': True},
                    'operations': [{'id': 'replace-documents', 'op': 'replace', 'path': '$', 'value': b_docs}],
                    'folder_action': 'replace_all_documents',
                    'document_mode': 'all',
                }
                if path_variables:
                    cfg['operations'] = generalize_operations(cfg['operations'], path_variables)
                if exact_bytes:
                    cfg['yaml_exact_bytes_base64'] = base64.b64encode(b.read_bytes()).decode('ascii')
                strategy = 'replace-all-documents'
                verified_one = True
                warnings = ['Multi-document change used replace-all-documents.']
            dump_one(cfg, cfg_path)
            entries.append(FolderEntry(rel, 'patch', cfg_rel, strategy, verified_one, warnings))

        manifest_files = []
        for entry in entries:
            row = asdict(entry)
            row.pop('warnings', None)
            manifest_files.append(row)
        manifest = {
            'version': 1,
            'kind': 'yaml-folder-patch',
            'source_root': str(before_root),
            'expected_root': str(after_root),
            'filters': {'include': include, 'exclude': exclude, 'path_allow': path_allow or [], 'path_deny': path_deny or [], 'fab_allow_prefix': fab_allow_prefix or [], 'fab_deny_prefix': fab_deny_prefix or [], 'env_allow': env_allow or [], 'env_deny': env_deny or []},
            'files': manifest_files,
        }
        log_lines = []
        for entry in entries:
            for warning in entry.warnings or []:
                log_lines.append(f'WARNING [{entry.relative_path}] {warning}')
        (output_root / 'log.txt').write_text(('\n'.join(log_lines) + ('\n' if log_lines else '')), encoding='utf-8')
        manifest_path = output_root / 'manifest.yaml'
        dump_one(manifest, manifest_path)
        compact_path = self._write_compact_patch(output_root, entries, source_root=before_root, path_variables=path_variables, variable_map=compiled_variable_map)
        verified_all = self.verify_manifest(before_root, output_root, after_root, variables=path_variables, variable_map_files=variable_map_files) if verify else all(x.verified for x in entries)
        manifest['verified'] = verified_all
        dump_one(manifest, manifest_path)
        if layout == 'compact':
            configs_dir = output_root / 'configs'
            if configs_dir.exists():
                shutil.rmtree(configs_dir)
            manifest_path.unlink(missing_ok=True)
            manifest_path = compact_path
        return FolderCompileResult(manifest_path, entries, verified_all, compact_path)

    def apply_manifest(
        self,
        source_root: str | Path,
        generated_root: str | Path,
        output_root: str | Path,
        *,
        variables: dict[str, Any] | None = None,
        variable_map_files: list[str | Path] | None = None,
    ) -> None:
        source_root = Path(source_root).resolve()
        generated_root = Path(generated_root).resolve()
        output_root = Path(output_root).resolve()
        if generated_root.is_file():
            return self.apply_compact(source_root, generated_root, output_root, variables=variables, variable_map_files=variable_map_files)
        manifest_path = generated_root / 'manifest.yaml'
        if not manifest_path.exists() and (generated_root / 'patch.yaml').exists():
            return self.apply_compact(source_root, generated_root / 'patch.yaml', output_root, variables=variables, variable_map_files=variable_map_files)
        manifest = load_one(manifest_path)
        if output_root.exists():
            shutil.rmtree(output_root)
        shutil.copytree(source_root, output_root)

        for entry in manifest.get('files', []):
            rel = Path(entry['relative_path'])
            target = output_root / rel
            action = entry['action']
            if action == 'unchanged':
                continue
            if action == 'delete':
                if target.exists():
                    target.unlink()
                continue
            cfg_path = generated_root / entry['config']
            cfg_raw = load_one(cfg_path)
            folder_action = cfg_raw.get('folder_action')
            if action == 'create':
                target.parent.mkdir(parents=True, exist_ok=True)
                if cfg_raw.get('yaml_exact_bytes_base64'):
                    target.write_bytes(base64.b64decode(cfg_raw['yaml_exact_bytes_base64']))
                else:
                    docs = cfg_raw['operations'][0]['value']
                    dump_all(docs, target)
            elif folder_action in {'replace_all_documents', 'replace_exact_bytes'}:
                target.parent.mkdir(parents=True, exist_ok=True)
                if cfg_raw.get('yaml_exact_bytes_base64'):
                    target.write_bytes(base64.b64decode(cfg_raw['yaml_exact_bytes_base64']))
                else:
                    docs = cfg_raw['operations'][0]['value']
                    dump_all(docs, target)
            else:
                self.engine.apply_file(target, cfg_path, target, variables or {}, variable_map_files=variable_map_files)


    @staticmethod
    def _rule_filters(rule: dict[str, Any]) -> dict[str, list[str]]:
        raw = rule.get('filters') or {}
        aliases = {
            'path_allow': ('path_allow', 'path_whitelist', 'paths', 'include_paths'),
            'path_deny': ('path_deny', 'path_blacklist', 'exclude_paths'),
            'fab_allow_prefix': ('fab_allow_prefix', 'fab_whitelist', 'fab_allow'),
            'fab_deny_prefix': ('fab_deny_prefix', 'fab_blacklist', 'fab_deny'),
            'env_allow': ('env_allow', 'env_whitelist'),
            'env_deny': ('env_deny', 'env_blacklist'),
        }
        result: dict[str, list[str]] = {}
        for canonical, names in aliases.items():
            value = next((raw.get(name) for name in names if name in raw), [])
            if value is None:
                value = []
            if isinstance(value, str):
                value = [value]
            result[canonical] = [str(x) for x in value]
        return result

    @classmethod
    def _rule_matches(cls, rel: Path, rule: dict[str, Any]) -> bool:
        filters = cls._rule_filters(rule)
        return cls._path_allowed(rel, **filters)

    @staticmethod
    def _operation_target(op: dict[str, Any]) -> str:
        path = op.get('path') or op.get('to_path') or op.get('target_path') or '$'
        if op.get('op') in {'update_item','remove_item','move_item','copy_item'}:
            match = op.get('match') or op.get('source', {}).get('match')
            return f"{path}::{match}"
        if op.get('op') in {'insert_key','rename_key','copy_key','move_key'}:
            key = op.get('key') or op.get('new_key') or op.get('target_key') or op.get('source_key')
            return f"{path}::{key}"
        return str(path)

    def plan_rules_config(self, source_root: str | Path, config: str | Path | dict[str, Any]) -> dict[str, Any]:
        """Preview rule scope and conservative write conflicts without modifying files."""
        source_root = Path(source_root).resolve()
        raw = load_config_with_variable_maps(config) if isinstance(config, (str, Path)) else config
        cfg = self.engine.load_config(config) if isinstance(config, (str, Path)) else EngineConfig.model_validate(raw)
        ordered = sorted(enumerate(cfg.rules), key=lambda pair: (-pair[1].get('priority', 0), pair[0]))
        files=[]; conflicts=[]; counts={r['id']:0 for r in cfg.rules}
        for target in sorted(source_root.rglob('*')):
            if not target.is_file() or target.suffix.lower() not in YAML_SUFFIXES or target.name == 'yaml-config-report.yaml': continue
            rel=target.relative_to(source_root)
            matched=[]; writes={}
            for _, rule in ordered:
                if not rule.get('enabled', True) or not self._rule_matches(rel, rule): continue
                matched.append(rule['id']); counts[rule['id']]+=1
                for op in rule['operations']:
                    sig=self._operation_target(op)
                    if sig in writes and writes[sig] != rule['id']:
                        conflicts.append({'relative_path':rel.as_posix(),'target':sig,'rules':[writes[sig],rule['id']]})
                    else: writes[sig]=rule['id']
                if rule.get('stop', False): break
            if matched or cfg.operations: files.append({'relative_path':rel.as_posix(),'rules':matched})
        policy=cfg.options.get('conflict_policy','warn')
        return {'version':1,'kind':'yaml-rules-plan','source_root':str(source_root),'files':files,
                'summary':{'matched_files':len(files),'rules':counts,'conflicts':len(conflicts)},
                'conflicts':conflicts,'conflict_policy':policy,'valid': not(conflicts and policy=='error')}

    def apply_rules_config(
        self,
        source_root: str | Path,
        config: str | Path | dict[str, Any],
        output_root: str | Path,
        *,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply per-file rules from one config to a copied folder tree.

        Rules are evaluated by descending priority and then declaration order.
        Multiple matching rules are cumulative unless a matching rule has stop: true.
        """
        source_root = Path(source_root).resolve()
        final_output_root = Path(output_root).resolve()
        raw = load_config_with_variable_maps(config) if isinstance(config, (str, Path)) else config
        cfg = self.engine.load_config(config) if isinstance(config, (str, Path)) else EngineConfig.model_validate(raw)
        if not cfg.rules and not cfg.operations:
            raise ValueError('Rules config requires rules or top-level operations')
        plan = self.plan_rules_config(source_root, raw)
        if not plan['valid']:
            raise ValueError(f"Rule conflicts detected: {plan['conflicts']}")
        final_output_root.parent.mkdir(parents=True, exist_ok=True)
        stage_root = Path(tempfile.mkdtemp(prefix=f'.{final_output_root.name}.stage-', dir=str(final_output_root.parent)))
        shutil.rmtree(stage_root)
        shutil.copytree(source_root, stage_root)
        output_root = stage_root

        ordered_rules = sorted(enumerate(cfg.rules), key=lambda pair: (-pair[1].get('priority', 0), pair[0]))
        report_files: list[dict[str, Any]] = []
        cli_vars = dict(variables or {})
        global_vars = dict(cfg.variables)
        for target in sorted(output_root.rglob('*')):
            if not target.is_file() or target.suffix.lower() not in YAML_SUFFIXES or target.name == 'yaml-config-report.yaml':
                continue
            rel = target.relative_to(output_root)
            parts = rel.parts
            fab = parts[0] if len(parts) >= 3 else ''
            env = parts[1] if len(parts) >= 3 else ''
            app_rel = Path(*parts[2:]).as_posix() if len(parts) >= 3 else rel.as_posix()
            file_context = {
                'FAB': fab, 'ENV': env, 'PATH': rel.as_posix(),
                'RELATIVE_PATH': rel.as_posix(), 'APP_PATH': app_rel,
                'FILE_NAME': target.name, 'FILE_STEM': target.stem,
            }
            scope_vars, matched_scopes = resolve_scope_variables(cfg.variable_map, fab, env)
            applied: list[str] = []
            applied_variable_scopes: list[str] = list(matched_scopes)
            changed = False
            if cfg.operations:
                base_cfg = {
                    'version': cfg.version, 'variables': cfg.variables, 'options': cfg.options,
                    'defaults': cfg.defaults, 'documents': cfg.documents, 'operations': cfg.operations,
                }
                result = self.engine.apply_file(target, base_cfg, target, {**global_vars, **scope_vars, **cli_vars, **file_context})
                changed = changed or result.changed
                applied.append('global')
            for _, rule in ordered_rules:
                if not rule.get('enabled', True) or not self._rule_matches(rel, rule):
                    continue
                rule_scope_vars, rule_scopes = resolve_scope_variables(rule.get('variable_map', {}), fab, env)
                applied_variable_scopes.extend(f'{rule["id"]}:{scope}' for scope in rule_scopes)
                rule_cfg = {
                    'version': cfg.version,
                    'variables': {**cfg.variables, **scope_vars, **rule_scope_vars, **rule.get('variables', {})},
                    'options': {**cfg.options, **rule.get('options', {})},
                    'defaults': {**cfg.defaults, **rule.get('defaults', {})},
                    'documents': rule.get('documents', cfg.documents),
                    'operations': rule['operations'],
                }
                result = self.engine.apply_file(target, rule_cfg, target, {**global_vars, **scope_vars, **rule_scope_vars, **rule.get('variables', {}), **cli_vars, **file_context})
                changed = changed or result.changed
                applied.append(rule['id'])
                if rule.get('stop', False):
                    break
            if applied:
                report_files.append({'relative_path': rel.as_posix(), 'changed': changed, 'rules': applied, 'variable_scopes': applied_variable_scopes})

        report = {
            'version': 1, 'kind': 'yaml-rules-apply-report',
            'source_root': str(source_root), 'output_root': str(output_root),
            'files': report_files,
            'summary': {
                'matched_files': len(report_files),
                'changed_files': sum(1 for x in report_files if x['changed']),
                'rules': {rule['id']: sum(rule['id'] in x['rules'] for x in report_files) for rule in cfg.rules},
            },
        }
        report['plan'] = plan['summary']
        report['output_root'] = str(final_output_root)
        dump_one(report, output_root / 'yaml-config-report.yaml')
        if final_output_root.exists():
            shutil.rmtree(final_output_root)
        os.replace(output_root, final_output_root)
        return report

    def check_rules_idempotency(self, source_root: str | Path, config: str | Path | dict[str, Any], variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Apply rules twice and verify that the second run changes no YAML content."""
        with TemporaryDirectory(prefix='yaml-idempotency-') as tmp:
            first=Path(tmp)/'first'; second=Path(tmp)/'second'
            self.apply_rules_config(source_root, config, first, variables=variables)
            self.apply_rules_config(first, config, second, variables=variables)
            def yaml_map(root: Path) -> dict[str, Any]:
                result={}
                for f in root.rglob('*'):
                    if f.is_file() and f.suffix.lower() in YAML_SUFFIXES and f.name != 'yaml-config-report.yaml':
                        result[f.relative_to(root).as_posix()] = load_all(f)
                return result
            a,b=yaml_map(first),yaml_map(second)
            changed=sorted(k for k in set(a)|set(b) if k not in a or k not in b or not strict_documents_equal(a[k], b[k]))
            return {'idempotent':not changed,'changed_on_second_run':changed,'checked_files':len(set(a)|set(b))}

    def verify_manifest(
        self,
        source_root: str | Path,
        generated_root: str | Path,
        expected_root: str | Path,
        *,
        variables: dict[str, Any] | None = None,
        variable_map_files: list[str | Path] | None = None,
    ) -> bool:
        expected_root = Path(expected_root).resolve()
        with TemporaryDirectory(prefix='yaml-folder-verify-') as tmp:
            actual_root = Path(tmp) / 'actual'
            self.apply_manifest(source_root, generated_root, actual_root, variables=variables, variable_map_files=variable_map_files)
            generated_path = Path(generated_root)
            manifest_path = generated_path / 'manifest.yaml' if generated_path.is_dir() else None
            if manifest_path is not None and manifest_path.exists():
                manifest = load_one(manifest_path)
                include = manifest.get('filters', {}).get('include') or ['**/*.yaml', '**/*.yml', '*.yaml', '*.yml']
                exclude = manifest.get('filters', {}).get('exclude') or []
                filters = manifest.get('filters', {})
            else:
                include = ['**/*.yaml', '**/*.yml', '*.yaml', '*.yml']
                exclude = []
                filters = {}
            kwargs = dict(path_allow=filters.get('path_allow'), path_deny=filters.get('path_deny'),
                fab_allow_prefix=filters.get('fab_allow_prefix'), fab_deny_prefix=filters.get('fab_deny_prefix'),
                env_allow=filters.get('env_allow'), env_deny=filters.get('env_deny'))
            actual = self._collect(actual_root, include, exclude, **kwargs)
            expected = self._collect(expected_root, include, exclude, **kwargs)
            if set(actual) != set(expected):
                return False
            return all(strict_documents_equal(load_all(actual[rel]), load_all(expected[rel])) for rel in expected)

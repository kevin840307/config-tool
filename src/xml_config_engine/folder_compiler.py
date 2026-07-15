from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import base64
from typing import Any

from yaml_config_engine.yamlio import dump_one, load_one
from yaml_config_engine.config_loader import load_config_with_variable_maps
from yaml_config_engine.variable_scope import resolve_scope_variables
from .compiler import XmlDiffCompiler
from .engine import XmlPatchEngine


@dataclass
class XmlFolderCompileResult:
    manifest_path: Path
    entries: list[dict[str, Any]]
    verified: bool
    compact_path: Path | None = None


class XmlFolderCompiler:
    @staticmethod
    def _readable_text_spec(data: bytes) -> dict[str, Any] | None:
        bom = data.startswith(b'\xef\xbb\xbf')
        raw = data[3:] if bom else data
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            return None
        return {'create_text': text, 'text_options': {'encoding': 'utf-8', 'bom': bom}}

    @staticmethod
    def _write_text_spec(target: Path, spec: dict[str, Any]) -> None:
        options = dict(spec.get('text_options') or {})
        data = str(spec['create_text']).encode(options.get('encoding', 'utf-8'))
        if options.get('bom'):
            data = b'\xef\xbb\xbf' + data
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    @staticmethod
    def _scope_for(relative_path: str) -> tuple[str, str]:
        parts = Path(relative_path).parts
        return (parts[0], parts[1]) if len(parts) >= 3 else ('', '')

    def _write_compact(self, out: Path, entries: list[dict[str, Any]]) -> Path:
        files: dict[str, Any] = {}
        for entry in entries:
            action = entry['action']
            rel = entry['relative_path']
            if action == 'unchanged':
                continue
            if action == 'delete':
                files[rel] = {'delete': True}
            elif action == 'create':
                data = (out / entry['payload']).read_bytes()
                files[rel] = self._readable_text_spec(data) or {
                    'create_bytes_base64': base64.b64encode(data).decode('ascii')
                }
            elif action == 'patch':
                cfg = load_one(out / entry['config'])
                files[rel] = {
                    'config': cfg,
                    'strategy': entry.get('strategy'),
                    'warnings': entry.get('warnings') or [],
                }
        patch = {
            'version': 1,
            'kind': 'xml-folder-patch-compact',
            'files': files,
            'summary': {k: sum(1 for e in entries if e['action'] == k) for k in ('patch', 'create', 'delete', 'unchanged')},
        }
        path = out / 'patch.yaml'
        dump_one(patch, path)
        return path

    def compile_folder(self, before_root, after_root, output_root, include_unchanged=False, verify=True, layout='compact', matched_files_only=False):
        if layout not in {'compact', 'expanded'}:
            raise ValueError("layout must be 'compact' or 'expanded'")
        b = Path(before_root).resolve(); a = Path(after_root).resolve(); out = Path(output_root).resolve()
        if out.exists(): shutil.rmtree(out)
        out.mkdir(parents=True)
        rels = sorted({p.relative_to(b) for p in b.rglob('*.xml')} | {p.relative_to(a) for p in a.rglob('*.xml')})
        entries = []; all_ok = True
        for rel in rels:
            bp = b / rel; ap = a / rel
            if matched_files_only and (not bp.exists() or not ap.exists()):
                continue
            if not bp.exists():
                dst = out / 'created' / rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(ap, dst)
                entries.append({'relative_path': rel.as_posix(), 'action': 'create', 'payload': str(Path('created') / rel)})
                continue
            if not ap.exists():
                entries.append({'relative_path': rel.as_posix(), 'action': 'delete'}); continue
            if bp.read_bytes() == ap.read_bytes():
                if include_unchanged: entries.append({'relative_path': rel.as_posix(), 'action': 'unchanged'})
                continue
            r = XmlDiffCompiler().compile_files(bp, ap)
            cfgrel = Path('configs') / rel.with_suffix(rel.suffix + '.config.yaml')
            cfgp = out / cfgrel; cfgp.parent.mkdir(parents=True, exist_ok=True); dump_one(r.config, cfgp)
            ok = r.verified
            if verify:
                extra = r.config
                if extra.get('xml_action') == 'replace_entire_file':
                    actual = str(extra.get('xml_exact_text', ''))
                else:
                    actual = XmlPatchEngine().apply_text(bp.read_text(encoding='utf-8-sig'), r.config)[0]
                ok = XmlDiffCompiler._structural_equal(actual, ap.read_text(encoding='utf-8-sig'))
            all_ok &= ok
            entries.append({'relative_path': rel.as_posix(), 'action': 'patch', 'config': cfgrel.as_posix(), 'verified': ok, 'strategy': r.strategy, 'warnings': r.warnings})
        manifest = {
            'version': 1, 'kind': 'xml-folder-manifest', 'before_root': str(b), 'after_root': str(a),
            'entries': entries, 'verified': all_ok,
            'counts': {k: sum(1 for e in entries if e['action'] == k) for k in ('patch', 'create', 'delete', 'unchanged')},
        }
        mp = out / 'manifest.yaml'; dump_one(manifest, mp)
        compact = self._write_compact(out, entries)
        if layout == 'compact':
            for name in ('configs', 'created'):
                d = out / name
                if d.exists(): shutil.rmtree(d)
            mp.unlink(missing_ok=True)
            mp = compact
        return XmlFolderCompileResult(mp, entries, all_ok, compact)

    def _apply_compact(self, src: Path, patch_path: Path, out: Path, variables: dict[str, Any] | None, variable_map_files: list[str | Path] | None = None):
        patch = load_config_with_variable_maps(patch_path, variable_map_files)
        if patch.get('kind') != 'xml-folder-patch-compact':
            raise ValueError(f'Not an XML compact folder patch: {patch_path}')
        if out.exists(): shutil.rmtree(out)
        shutil.copytree(src, out)
        report = []
        for rel, spec in (patch.get('files') or {}).items():
            target = out / rel
            if spec.get('delete') is True:
                if target.exists(): target.unlink()
                action = 'delete'
            elif 'create_bytes_base64' in spec:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(spec['create_bytes_base64']))
                action = 'create'
            elif 'create_text' in spec:
                # Backward compatibility with compact patches generated before
                # exact-byte payloads were introduced.
                self._write_text_spec(target, spec)
                action = 'create'
            else:
                fab, env = self._scope_for(rel)
                scope_vars, _ = resolve_scope_variables(patch.get('variable_map', {}), fab, env)
                merged = dict(patch.get('variables') or {}); merged.update(scope_vars); merged.update(variables or {})
                cfg = dict(spec.get('config') or {})
                XmlPatchEngine().apply_file(target, cfg, target, merged)
                action = 'patch'
            report.append({'relative_path': rel, 'action': action})
        counts = {k: sum(1 for e in report if e['action'] == k) for k in ('patch', 'create', 'delete')}
        return {'version': 1, 'kind': 'xml-folder-apply', 'output_root': str(out), 'files': report, 'counts': counts}

    def apply_folder(self, source_root, generated_root, output_root, variables=None, variable_map_files=None):
        src = Path(source_root).resolve(); gen = Path(generated_root).resolve(); out = Path(output_root).resolve()
        if gen.is_file():
            return self._apply_compact(src, gen, out, variables, variable_map_files)
        if not (gen / 'manifest.yaml').exists() and (gen / 'patch.yaml').exists():
            return self._apply_compact(src, gen / 'patch.yaml', out, variables, variable_map_files)
        manifest = load_one(gen / 'manifest.yaml')
        if out.exists(): shutil.rmtree(out)
        shutil.copytree(src, out); report = []
        for e in manifest['entries']:
            target = out / e['relative_path']; action = e['action']
            if action == 'patch': XmlPatchEngine().apply_file(target, gen / e['config'], target, variables or {}, variable_map_files=variable_map_files)
            elif action == 'create': target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(gen / e['payload'], target)
            elif action == 'delete' and target.exists(): target.unlink()
            report.append({'relative_path': e['relative_path'], 'action': action})
        return {'version': 1, 'kind': 'xml-folder-apply', 'output_root': str(out), 'files': report, 'counts': manifest.get('counts', {})}

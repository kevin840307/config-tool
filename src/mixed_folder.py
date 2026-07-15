from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import base64
import shutil

from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.yamlio import load_one, load_all, dump_one, dump_all
from yaml_config_engine.config_loader import load_config_with_variable_maps
from yaml_config_engine.variable_scope import resolve_scope_variables
from yaml_config_engine.comparison import strict_documents_equal
from xml_config_engine.folder_compiler import XmlFolderCompiler
from xml_config_engine.engine import XmlPatchEngine
from xml_config_engine.compiler import XmlDiffCompiler


class MixedFolderCompiler:
    """Compile/apply YAML and XML changes in one folder patch."""

    YAML_SUFFIXES = {'.yaml', '.yml'}
    XML_SUFFIXES = {'.xml'}

    @staticmethod
    def _patch_path(generated_root: str | Path) -> Path:
        path = Path(generated_root).resolve()
        return path if path.is_file() else path / 'patch.yaml'

    @staticmethod
    def _readable_text_spec(data: bytes, action: str) -> dict[str, Any] | None:
        bom = data.startswith(b'\xef\xbb\xbf')
        raw = data[3:] if bom else data
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            return None
        line_ending = 'crlf' if b'\r\n' in raw else 'lf'
        return {f'{action}_text': text, 'text_options': {'encoding': 'utf-8', 'bom': bom, 'line_ending': line_ending}}

    @staticmethod
    def _write_text_spec(target: Path, spec: dict[str, Any], key: str) -> None:
        options = dict(spec.get('text_options') or {})
        encoding = options.get('encoding', 'utf-8')
        text = str(spec[key])
        data = text.encode(encoding)
        if options.get('bom') and encoding.lower().replace('_', '-') == 'utf-8':
            data = b'\xef\xbb\xbf' + data
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def _expected_snapshot(self, before: Path, after: Path, matched_files_only: bool) -> dict[Path, bytes]:
        after_snap = self.snapshot(after)
        if not matched_files_only:
            return after_snap
        before_snap = self.snapshot(before)
        common = set(before_snap) & set(after_snap)
        expected = dict(before_snap)
        for rel in common:
            expected[rel] = after_snap[rel]
        return expected

    def _trees_equal(self, actual: Path, expected: Path, *, exact_bytes: bool = False) -> bool:
        actual_files = {p.relative_to(actual) for p in actual.rglob('*') if p.is_file()}
        expected_files = {p.relative_to(expected) for p in expected.rglob('*') if p.is_file()}
        if actual_files != expected_files:
            return False
        for rel in sorted(actual_files):
            a = actual / rel
            e = expected / rel
            if exact_bytes:
                if a.read_bytes() != e.read_bytes():
                    return False
                continue
            suffix = rel.suffix.lower()
            if suffix in self.YAML_SUFFIXES:
                if not strict_documents_equal(load_all(a), load_all(e)):
                    return False
            elif suffix in self.XML_SUFFIXES:
                at = a.read_text(encoding='utf-8-sig')
                et = e.read_text(encoding='utf-8-sig')
                if not XmlDiffCompiler._structural_equal(at, et):
                    return False
            elif a.read_bytes() != e.read_bytes():
                return False
        return True

    def compile_folder(
        self,
        before_root: str | Path,
        after_root: str | Path,
        output_root: str | Path,
        *,
        include_unchanged: bool = False,
        verify: bool = True,
        layout: str = 'compact',
        matched_files_only: bool = False,
        exact_bytes: bool = False,
    ) -> dict[str, Any]:
        if layout not in {'compact', 'expanded'}:
            raise ValueError("layout must be 'compact' or 'expanded'")
        before = Path(before_root).resolve()
        after = Path(after_root).resolve()
        out = Path(output_root).resolve()
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)

        with TemporaryDirectory(prefix='mixed-folder-compile-') as tmp:
            tmp_root = Path(tmp)
            yaml_out = tmp_root / 'yaml'
            xml_out = tmp_root / 'xml'
            yres = FolderCompiler().compile_folder(
                before, after, yaml_out,
                include_unchanged=include_unchanged,
                verify=verify,
                layout='expanded',
                matched_files_only=matched_files_only,
                exact_bytes=exact_bytes,
            )
            xres = XmlFolderCompiler().compile_folder(
                before, after, xml_out,
                include_unchanged=include_unchanged,
                verify=verify,
                layout='expanded',
                matched_files_only=matched_files_only,
            )

            if layout == 'expanded':
                entries: list[dict[str, Any]] = []
                ymanifest = load_one(yaml_out / 'manifest.yaml')
                for e in ymanifest.get('files', []):
                    item = dict(e); item['format'] = 'yaml'
                    if item.get('config'):
                        src = yaml_out / item['config']
                        rel_cfg = Path('configs/yaml') / Path(item['config']).relative_to('configs')
                        dst = out / rel_cfg; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
                        item['config'] = rel_cfg.as_posix()
                    entries.append(item)
                xmanifest = load_one(xml_out / 'manifest.yaml')
                for e in xmanifest.get('entries', []):
                    item = dict(e); item['format'] = 'xml'
                    if item.get('config'):
                        src = xml_out / item['config']
                        rel_cfg = Path('configs/xml') / Path(item['config']).relative_to('configs')
                        dst = out / rel_cfg; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
                        item['config'] = rel_cfg.as_posix()
                    if item.get('payload'):
                        src = xml_out / item['payload']
                        rel_payload = Path('payloads/xml') / Path(item['payload']).relative_to('created')
                        dst = out / rel_payload; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
                        item['payload'] = rel_payload.as_posix()
                    entries.append(item)
                manifest = {
                    'version': 1,
                    'kind': 'mixed-folder-manifest',
                    'layout': 'expanded',
                    'matched_files_only': matched_files_only,
                    'entries': sorted(entries, key=lambda x: (x['relative_path'], x['format'])),
                    'verified': bool(yres.verified and xres.verified),
                    'counts': {k: sum(1 for e in entries if e.get('action') == k) for k in ('patch','create','delete','unchanged')},
                }
                manifest_path = out / 'manifest.yaml'; dump_one(manifest, manifest_path)
                if verify:
                    with TemporaryDirectory(prefix='mixed-expanded-verify-') as verify_tmp:
                        actual = Path(verify_tmp) / 'actual'
                        self.apply_folder(before, out, actual)
                        if matched_files_only:
                            expected_tree = Path(verify_tmp) / 'expected'
                            shutil.copytree(before, expected_tree)
                            after_snap = self.snapshot(after)
                            before_snap = self.snapshot(before)
                            for rel in set(before_snap) & set(after_snap):
                                target = expected_tree / rel
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(after_snap[rel])
                        else:
                            expected_tree = after
                        manifest['verified'] = self._trees_equal(actual, expected_tree, exact_bytes=exact_bytes)
                        dump_one(manifest, manifest_path)
                return {'verified': manifest['verified'], 'manifest': str(manifest_path), 'summary': manifest['counts'], 'layout': 'expanded'}

            ymanifest = load_one(yaml_out / 'manifest.yaml')
            xmanifest = load_one(xml_out / 'manifest.yaml')

            # Compact patch is composed from the exact per-file configs created
            # by the single-file compilers. This keeps compact and expanded
            # behavior identical; only the packaging differs.
            files: dict[str, Any] = {}
            for entry in ymanifest.get('files', []):
                rel = entry['relative_path']; action = entry['action']
                if action == 'unchanged':
                    continue
                if action == 'delete':
                    files[rel] = {'format': 'yaml', 'delete': True}
                    continue
                cfg = load_one(yaml_out / entry['config'])
                if action == 'create' or cfg.get('folder_action') in {'replace_all_documents', 'replace_exact_bytes'}:
                    key_action = 'create' if action == 'create' else 'replace'
                    if cfg.get('yaml_exact_bytes_base64'):
                        data = base64.b64decode(cfg['yaml_exact_bytes_base64'])
                        readable = self._readable_text_spec(data, key_action)
                        payload = readable or {f'{key_action}_bytes_base64': cfg['yaml_exact_bytes_base64']}
                        files[rel] = {'format': 'yaml', **payload, 'strategy': entry.get('strategy')}
                    else:
                        docs = (cfg.get('operations') or [{}])[0].get('value', [])
                        files[rel] = {'format': 'yaml', f'{key_action}_documents': docs, 'strategy': entry.get('strategy')}
                else:
                    files[rel] = {'format': 'yaml', 'config': cfg, 'strategy': entry.get('strategy'), 'warnings': entry.get('warnings') or []}

            for entry in xmanifest.get('entries', []):
                rel = entry['relative_path']; action = entry['action']
                if action == 'unchanged':
                    continue
                if rel in files:
                    raise ValueError(f'Duplicate mixed-folder entry: {rel}')
                if action == 'delete':
                    files[rel] = {'format': 'xml', 'delete': True}
                elif action == 'create':
                    data = (xml_out / entry['payload']).read_bytes()
                    readable = self._readable_text_spec(data, 'create')
                    payload = readable or {'create_bytes_base64': base64.b64encode(data).decode('ascii')}
                    files[rel] = {'format': 'xml', **payload}
                else:
                    cfg = load_one(xml_out / entry['config'])
                    files[rel] = {'format': 'xml', 'config': cfg, 'strategy': entry.get('strategy'), 'warnings': entry.get('warnings') or []}

        summary = {'yaml': dict(ymanifest.get('counts') or {}), 'xml': dict(xmanifest.get('counts') or {})}
        summary['total'] = {key: int(summary['yaml'].get(key, 0)) + int(summary['xml'].get(key, 0)) for key in ('patch','create','delete','unchanged')}
        patch = {'version': 1, 'kind': 'mixed-folder-patch-compact', 'formats': ['yaml','xml'], 'matched_files_only': matched_files_only, 'files': dict(sorted(files.items())), 'summary': summary}
        patch_path = out / 'patch.yaml'; dump_one(patch, patch_path)

        verified = bool(yres.verified and xres.verified)
        if verify:
            with TemporaryDirectory(prefix='mixed-folder-verify-') as tmp:
                actual = Path(tmp) / 'actual'
                self.apply_folder(before, patch_path, actual)
                if matched_files_only:
                    expected_tree = Path(tmp) / 'expected'
                    shutil.copytree(before, expected_tree)
                    after_snap = self.snapshot(after)
                    before_snap = self.snapshot(before)
                    for rel in set(before_snap) & set(after_snap):
                        target = expected_tree / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(after_snap[rel])
                else:
                    expected_tree = after
                if exact_bytes and not self._trees_equal(actual, expected_tree, exact_bytes=True):
                    actual_snap = self.snapshot(actual); expected_snap = self.snapshot(expected_tree)
                    mismatches = [rel for rel in sorted(set(actual_snap) | set(expected_snap)) if actual_snap.get(rel) != expected_snap.get(rel)]
                    before_snap = self.snapshot(before)
                    for rel in mismatches:
                        suffix = rel.suffix.lower()
                        if suffix not in self.YAML_SUFFIXES | self.XML_SUFFIXES or rel not in expected_snap:
                            continue
                        action = 'create' if rel not in before_snap else 'replace'
                        readable = self._readable_text_spec(expected_snap[rel], action)
                        fmt = 'xml' if suffix in self.XML_SUFFIXES else 'yaml'
                        if readable is not None:
                            files[rel.as_posix()] = {'format': fmt, **readable, 'strategy': 'readable-exact-text'}
                        else:
                            files[rel.as_posix()] = {'format': fmt, f'{action}_bytes_base64': base64.b64encode(expected_snap[rel]).decode('ascii'), 'strategy': 'exact-bytes'}
                    if mismatches:
                        patch['files'] = dict(sorted(files.items())); dump_one(patch, patch_path)
                        shutil.rmtree(actual); self.apply_folder(before, patch_path, actual)
                verified = self._trees_equal(actual, expected_tree, exact_bytes=exact_bytes)
        return {'verified': verified, 'patch': str(patch_path), 'summary': summary, 'layout': 'compact'}

    @staticmethod
    def snapshot(root: str | Path) -> dict[Path, bytes]:
        root = Path(root)
        return {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    @staticmethod
    def _scope_for(relative_path: str) -> tuple[str, str]:
        parts = Path(relative_path).parts
        return (parts[0], parts[1]) if len(parts) >= 3 else ('', '')

    def apply_folder(
        self,
        source_root: str | Path,
        generated_root: str | Path,
        output_root: str | Path,
        variables: dict[str, Any] | None = None,
        variable_map_files: list[str | Path] | None = None,
    ) -> dict[str, Any]:
        source = Path(source_root).resolve()
        generated = Path(generated_root).resolve()
        output = Path(output_root).resolve()
        manifest_path = generated / 'manifest.yaml' if generated.is_dir() else None
        if manifest_path is not None and manifest_path.exists():
            manifest = load_one(manifest_path)
            if manifest.get('kind') == 'mixed-folder-manifest':
                if output.exists(): shutil.rmtree(output)
                shutil.copytree(source, output)
                yaml_engine = FolderCompiler().engine
                xml_engine = XmlPatchEngine()
                report = []
                for entry in manifest.get('entries', []):
                    rel = entry['relative_path']; fmt = entry['format']; action = entry['action']; target = output / rel
                    if action == 'unchanged':
                        continue
                    if action == 'delete':
                        if target.exists(): target.unlink()
                    elif action == 'create':
                        if fmt == 'yaml':
                            cfg = load_one(generated / entry['config'])
                            target.parent.mkdir(parents=True, exist_ok=True)
                            if cfg.get('yaml_exact_bytes_base64'):
                                target.write_bytes(base64.b64decode(cfg['yaml_exact_bytes_base64']))
                            else:
                                dump_all(cfg['operations'][0]['value'], target)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(generated / entry['payload'], target)
                    elif action == 'patch':
                        cfg_path = generated / entry['config']
                        if fmt == 'yaml': yaml_engine.apply_file(target, cfg_path, target, variables or {}, variable_map_files=variable_map_files)
                        else: xml_engine.apply_file(target, cfg_path, target, variables or {}, variable_map_files=variable_map_files)
                    report.append({'relative_path': rel, 'format': fmt, 'action': action})
                return {'version': 1, 'kind': 'mixed-folder-apply', 'output_root': str(output), 'files': report, 'counts': manifest.get('counts', {})}
        patch_path = self._patch_path(generated_root)
        patch = load_config_with_variable_maps(patch_path, variable_map_files)
        if patch.get('kind') != 'mixed-folder-patch-compact':
            raise ValueError(f'Not a mixed folder patch: {patch_path}')
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(source, output)

        yaml_compiler = FolderCompiler()
        xml_engine = XmlPatchEngine()
        report: list[dict[str, Any]] = []
        for rel, raw_spec in (patch.get('files') or {}).items():
            spec = dict(raw_spec or {})
            fmt = spec.pop('format', None)
            if fmt not in {'yaml', 'xml'}:
                raise ValueError(f'{rel}: format must be yaml or xml')
            target = output / rel
            if spec.get('delete') is True:
                if target.exists(): target.unlink()
                report.append({'relative_path': rel, 'format': fmt, 'action': 'delete'})
                continue

            if fmt == 'yaml':
                if 'create_bytes_base64' in spec or 'replace_bytes_base64' in spec:
                    encoded = spec.get('create_bytes_base64', spec.get('replace_bytes_base64'))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(base64.b64decode(encoded))
                    action = 'create' if 'create_bytes_base64' in spec else 'patch'
                elif 'create_text' in spec or 'replace_text' in spec:
                    key = 'create_text' if 'create_text' in spec else 'replace_text'
                    self._write_text_spec(target, spec, key)
                    action = 'create' if key == 'create_text' else 'patch'
                elif 'create_documents' in spec or 'replace_documents' in spec:
                    docs = spec.get('create_documents', spec.get('replace_documents'))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    dump_all(docs, target)
                    action = 'create' if 'create_documents' in spec else 'patch'
                else:
                    fab, env = self._scope_for(rel)
                    scope_vars, _ = resolve_scope_variables(patch.get('variable_map', {}), fab, env)
                    merged = dict(patch.get('variables') or {})
                    merged.update(scope_vars)
                    merged.update(variables or {})
                    if 'config' in spec:
                        cfg = dict(spec.get('config') or {})
                    else:
                        operations = [yaml_compiler._expand_compact_operation(op, i) for i, op in enumerate(spec.get('ops', []), 1)]
                        cfg = {
                            'version': 1,
                            'options': patch.get('options') or {'atomic_write': True},
                            'variables': patch.get('variables') or {},
                            'variable_map': patch.get('variable_map') or {},
                            'operations': operations,
                        }
                    yaml_compiler.engine.apply_file(target, cfg, target, merged)
                    action = 'patch'
            else:
                if 'create_bytes_base64' in spec or 'replace_bytes_base64' in spec:
                    encoded = spec.get('create_bytes_base64', spec.get('replace_bytes_base64'))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(base64.b64decode(encoded))
                    action = 'create' if 'create_bytes_base64' in spec else 'patch'
                elif 'create_text' in spec or 'replace_text' in spec:
                    key = 'create_text' if 'create_text' in spec else 'replace_text'
                    self._write_text_spec(target, spec, key)
                    action = 'create' if key == 'create_text' else 'patch'
                else:
                    fab, env = self._scope_for(rel)
                    scope_vars, _ = resolve_scope_variables(patch.get('variable_map', {}), fab, env)
                    merged = dict(patch.get('variables') or {})
                    merged.update(scope_vars)
                    merged.update(variables or {})
                    cfg = dict(spec.get('config') or {})
                    xml_engine.apply_file(target, cfg, target, merged)
                    action = 'patch'
            report.append({'relative_path': rel, 'format': fmt, 'action': action})

        counts = {
            fmt: {action: sum(1 for x in report if x['format'] == fmt and x['action'] == action)
                  for action in ('patch', 'create', 'delete')}
            for fmt in ('yaml', 'xml')
        }
        return {'version': 1, 'kind': 'mixed-folder-apply', 'output_root': str(output), 'files': report, 'counts': counts}

    def verify_folder(self, source_root: str | Path, generated_root: str | Path, expected_root: str | Path, *, exact_bytes: bool = False) -> bool:
        with TemporaryDirectory(prefix='mixed-folder-verify-') as tmp:
            actual = Path(tmp) / 'actual'
            self.apply_folder(source_root, generated_root, actual)
            return self._trees_equal(actual, Path(expected_root).resolve(), exact_bytes=exact_bytes)

from pathlib import Path
from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.yamlio import load_one, load_all
from yaml_config_engine.comparison import strict_documents_equal


def test_compile_folder_writes_compact_and_legacy_artifacts(tmp_path: Path):
    before = tmp_path / 'before'; after = tmp_path / 'after'; generated = tmp_path / 'generated'
    before.mkdir(); after.mkdir()
    (before / 'app.yaml').write_text('server:\n  port: 8080\nold: true\n', encoding='utf-8')
    (after / 'app.yaml').write_text('server:\n  port: 9090\n', encoding='utf-8')
    (after / 'new.yaml').write_text('enabled: true\n', encoding='utf-8')
    result = FolderCompiler().compile_folder(before, after, generated, layout='expanded')
    assert result.verified
    assert (generated / 'patch.yaml').exists()
    assert (generated / 'manifest.yaml').exists()
    assert (generated / 'configs' / 'app.yaml.patch.yaml').exists()
    patch = load_one(generated / 'patch.yaml')
    assert patch['kind'] == 'yaml-folder-patch-compact'
    assert set(patch['files']) == {'app.yaml', 'new.yaml'}
    assert patch['summary']['patch'] == 1
    assert patch['summary']['create'] == 1


def test_apply_folder_accepts_compact_patch_file(tmp_path: Path):
    before = tmp_path / 'before'; after = tmp_path / 'after'; generated = tmp_path / 'generated'; output = tmp_path / 'output'
    before.mkdir(); after.mkdir()
    (before / 'app.yaml').write_text('server:\n  port: 8080\nold: true\n', encoding='utf-8')
    (before / 'remove.yaml').write_text('x: 1\n', encoding='utf-8')
    (after / 'app.yaml').write_text('server:\n  port: 9090\n', encoding='utf-8')
    (after / 'new.yaml').write_text('enabled: true\n', encoding='utf-8')
    compiler = FolderCompiler()
    compiler.compile_folder(before, after, generated)
    compiler.apply_manifest(before, generated / 'patch.yaml', output)
    assert not (output / 'remove.yaml').exists()
    for name in ('app.yaml', 'new.yaml'):
        assert strict_documents_equal(load_all(output / name), load_all(after / name))


def test_compile_folder_default_outputs_only_patch_yaml(tmp_path: Path):
    before = tmp_path / 'before'; after = tmp_path / 'after'; generated = tmp_path / 'generated'; output = tmp_path / 'output'
    before.mkdir(); after.mkdir()
    (before / 'app.yaml').write_text('server:\n  port: 8080\n', encoding='utf-8')
    (after / 'app.yaml').write_text('server:\n  port: 9090\n', encoding='utf-8')
    result = FolderCompiler().compile_folder(before, after, generated)
    assert result.verified
    assert sorted(p.name for p in generated.iterdir()) == ['patch.yaml']
    FolderCompiler().apply_manifest(before, generated, output)
    assert load_one(output / 'app.yaml')['server']['port'] == 9090


def test_compact_patch_supports_external_variable_map(tmp_path: Path):
    before = tmp_path / 'before'; generated = tmp_path / 'generated'; output = tmp_path / 'output'
    target = before / 'FAB14-FZ1' / 'STAGING' / 'app' / 'application.yaml'
    target.parent.mkdir(parents=True)
    target.write_text('server:\n  host: old\n  timeout: 10\n', encoding='utf-8')
    generated.mkdir()
    (generated / 'variable-map.yaml').write_text(
        'variable_map:\n  FAB14:STAGING:\n    HOST: staging-host\n    TIMEOUT: 45\n', encoding='utf-8')
    (generated / 'patch.yaml').write_text(
        '''version: 1
kind: yaml-folder-patch-compact
variable_map_file: variable-map.yaml
files:
  FAB14-FZ1/STAGING/app/application.yaml:
    ops:
      - set: [$.server.host, "{{ HOST }}"]
      - set: [$.server.timeout, "{{ TIMEOUT }}"]
summary: {patch: 1, create: 0, delete: 0, unchanged: 0}
''', encoding='utf-8')
    FolderCompiler().apply_manifest(before, generated, output)
    actual = load_one(output / 'FAB14-FZ1' / 'STAGING' / 'app' / 'application.yaml')
    assert actual['server']['host'] == 'staging-host'
    assert str(actual['server']['timeout']) == '45'

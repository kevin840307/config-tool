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
    result = FolderCompiler().compile_folder(before, after, generated)
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

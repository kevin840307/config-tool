from yaml_config_engine.cli import main
from pathlib import Path


def test_cli_compile_verify(tmp_path):
    a = tmp_path/'a.yaml'; b = tmp_path/'b.yaml'; c = tmp_path/'c.yaml'
    a.write_text('x: 1\n', encoding='utf-8'); b.write_text('x: 2\n', encoding='utf-8')
    assert main(['compile', str(a), str(b), '-o', str(c)]) == 0
    assert main(['verify', str(a), str(c), str(b)]) == 0


def test_cli_compile_folder_apply_and_verify(tmp_path):
    before = tmp_path / 'before'; after = tmp_path / 'after'; generated = tmp_path / 'generated'; output = tmp_path / 'output'
    (before/'FAB14-FZ1'/'STAGING'/'app').mkdir(parents=True)
    (after/'FAB14-FZ1'/'STAGING'/'app').mkdir(parents=True)
    (before/'FAB14-FZ1'/'STAGING'/'app'/'a.yaml').write_text('# keep\ndb:\n  - version: "2025.6"\n    data: B\n', encoding='utf-8')
    (after/'FAB14-FZ1'/'STAGING'/'app'/'a.yaml').write_text('# keep\ndb:\n  - version: "2025.6"\n    data: B\n  - version: "2025.4"\n    data: B\n', encoding='utf-8')
    (after/'FAB14-FZ1'/'STAGING'/'app'/'new.yaml').write_text('new: true\n', encoding='utf-8')
    (before/'FAB14-FZ1'/'STAGING'/'app'/'old.yaml').write_text('old: true\n', encoding='utf-8')

    assert main(['compile-folder', str(before), str(after), str(generated), '--layout', 'expanded']) == 0
    assert (generated/'manifest.yaml').exists()
    assert main(['verify-folder', str(before), str(generated), str(after)]) == 0
    assert main(['apply-folder', str(before), str(generated), str(output)]) == 0
    assert (output/'FAB14-FZ1'/'STAGING'/'app'/'new.yaml').exists()
    assert not (output/'FAB14-FZ1'/'STAGING'/'app'/'old.yaml').exists()
    text = (output/'FAB14-FZ1'/'STAGING'/'app'/'a.yaml').read_text(encoding='utf-8')
    assert '# keep' in text

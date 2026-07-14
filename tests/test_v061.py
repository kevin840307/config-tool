from __future__ import annotations
from pathlib import Path

from yaml_config_engine.cli import main
from yaml_config_engine.linting import ConfigLinter
from yaml_config_engine.yamlio import load_one


def test_lint_detects_undefined_variable_and_duplicate_rule_id():
    config = {
        'rules': [
            {'id': 'same', 'operations': [{'op': 'set', 'path': '$.x', 'value': '{{ MISSING }}'}]},
            {'id': 'same', 'operations': [{'op': 'set', 'path': '$.y', 'value': 2}]},
        ]
    }
    report = ConfigLinter().lint(config)
    assert not report.valid
    assert {x.code for x in report.errors} == {'DUPLICATE_RULE_ID', 'UNDEFINED_VARIABLE'}


def test_lint_warns_non_idempotent_insert():
    report = ConfigLinter().lint({'operations': [{'op': 'append', 'path': '$.items', 'value': 'A'}]})
    assert report.valid
    assert 'POTENTIALLY_NON_IDEMPOTENT_INSERT' in {x.code for x in report.warnings}


def test_lint_with_source_detects_zero_match(tmp_path: Path):
    source = tmp_path/'source'; source.mkdir(); (source/'x.yaml').write_text('x: 1\n', encoding='utf-8')
    config = tmp_path/'config.yaml'
    config.write_text('''rules:\n  - id: r\n    filters:\n      path_allow: [missing.yaml]\n    operations:\n      - op: set\n        path: $.x\n        value: 2\n''', encoding='utf-8')
    assert main(['lint', str(config), '--source-root', str(source)]) == 2


def test_run_folder_safe_pipeline(tmp_path: Path):
    source = tmp_path/'source'; target = source/'FAB14'/'STAGING'/'app'/'application.yaml'
    target.parent.mkdir(parents=True); target.write_text('value: old\n', encoding='utf-8')
    config = tmp_path/'config.yaml'
    config.write_text('''variables:\n  NEW_VALUE: new\nrules:\n  - id: update\n    filters:\n      path_allow: [app/application.yaml]\n    operations:\n      - op: set\n        path: $.value\n        value: "{{ NEW_VALUE }}"\n''', encoding='utf-8')
    output = tmp_path/'output'
    assert main(['run-folder', str(source), str(config), str(output)]) == 0
    assert load_one(output/'FAB14'/'STAGING'/'app'/'application.yaml')['value'] == 'new'


def test_run_folder_blocks_non_idempotent_config(tmp_path: Path):
    source = tmp_path/'source'; source.mkdir(); (source/'x.yaml').write_text('items: []\n', encoding='utf-8')
    config = tmp_path/'config.yaml'
    config.write_text('''operations:\n  - op: append\n    path: $.items\n    value: A\n''', encoding='utf-8')
    output = tmp_path/'output'
    # It fails at lint by default because the unsafe insert warning is blocking in run-folder.
    assert main(['run-folder', str(source), str(config), str(output)]) == 2
    assert not output.exists()

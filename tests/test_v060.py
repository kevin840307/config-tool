from __future__ import annotations
from pathlib import Path
import subprocess, sys
from yaml_config_engine.comparison import strict_equal, strict_documents_equal
from yaml_config_engine.diff_compiler import DiffCompiler
from yaml_config_engine.engine import YamlPatchEngine
from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.variable_scope import resolve_scope_variables
from yaml_config_engine.yamlio import load_all, load_one, dump_one


def test_strict_compare_mapping_order_and_scalar_type():
    assert not strict_equal({'a': 1, 'b': 2}, {'b': 2, 'a': 1})
    assert not strict_equal({'a': 1}, {'a': True})
    assert strict_equal({'a': ['1', 2]}, {'a': ['1', 2]})


def test_compiler_enforces_mapping_order():
    before = {'old': 1, 'keep': 2}
    after = {'keep': 2, 'new': 1, 'extra': 3}
    result = DiffCompiler().compile(before, after)
    assert result.verified
    actual = YamlPatchEngine().apply_document(before.copy(), result.config)
    assert list(actual) == list(after)
    assert strict_equal(actual, after)


def test_special_key_paths_are_json_pointer_safe():
    before = {'a.b[c]': {'x/y': 1}, 'keep': True}
    after = {'a.b[c]': {'x/y': 2}, 'keep': True}
    result = DiffCompiler().compile(before, after)
    assert result.verified
    assert any('a.b[c]' in op.get('path', '') for op in result.config['operations'])
    actual = YamlPatchEngine().apply_document(before.copy(), result.config)
    assert strict_equal(actual, after)


def test_unhashable_identity_falls_back_safely():
    before = {'x': [{'name': ['A'], 'v': 1}]}
    after = {'x': [{'name': ['A'], 'v': 2}]}
    result = DiffCompiler().compile(before, after)
    assert result.verified
    actual = YamlPatchEngine().apply_document(before.copy(), result.config)
    assert strict_equal(actual, after)


def test_scope_variable_prefix_precedence():
    table = {
        'FAB14': {'XXX': 'A', 'GENERIC': 1},
        'FAB14-FZ1': {'XXX': 'B'},
        'FAB14-FZ1:STAG': {'XXX': 'C', 'ENV_ONLY': True},
    }
    values, scopes = resolve_scope_variables(table, 'FAB14-FZ1-A', 'STAGING')
    assert scopes == ['FAB14', 'FAB14-FZ1', 'FAB14-FZ1:STAG']
    assert values == {'XXX': 'C', 'GENERIC': 1, 'ENV_ONLY': True}


def test_rules_folder_uses_fab_env_variable_map(tmp_path: Path):
    source = tmp_path/'source'; target = source/'FAB14-FZ1'/'STAGING'/'app'/'application.yaml'
    target.parent.mkdir(parents=True); target.write_text('value: old\n', encoding='utf-8')
    config = {
        'version': 1,
        'variable_map': {
            'FAB14': {'XXX': 'A'},
            'FAB14-FZ1': {'XXX': 'B'},
            'FAB14-FZ1:STAGING': {'XXX': 'C'},
        },
        'rules': [{
            'id': 'set-value',
            'filters': {'path_allow': ['app/application.yaml']},
            'operations': [{'op': 'set', 'path': '$.value', 'value': '{{ XXX }}'}],
        }],
    }
    output = tmp_path/'out'
    report = FolderCompiler().apply_rules_config(source, config, output)
    assert load_one(output/'FAB14-FZ1'/'STAGING'/'app'/'application.yaml')['value'] == 'C'
    assert report['files'][0]['variable_scopes'] == ['FAB14', 'FAB14-FZ1', 'FAB14-FZ1:STAGING']


def test_rule_variable_map_overrides_global_and_cli_overrides_all(tmp_path: Path):
    source = tmp_path/'source'; target = source/'FAB14-FZ1'/'STAGING'/'app'/'application.yaml'
    target.parent.mkdir(parents=True); target.write_text('value: old\n', encoding='utf-8')
    config = {
        'variable_map': {'FAB14': {'XXX': 'global'}},
        'rules': [{
            'id': 'r',
            'variable_map': {'FAB14-FZ1:STAGING': {'XXX': 'rule-scope'}},
            'variables': {'OTHER': 'x'},
            'operations': [{'op': 'set', 'path': '$.value', 'value': '{{ XXX }}'}],
        }],
    }
    output = tmp_path/'out'
    FolderCompiler().apply_rules_config(source, config, output, variables={'XXX': 'cli'})
    assert load_one(output/'FAB14-FZ1'/'STAGING'/'app'/'application.yaml')['value'] == 'cli'


def test_multi_document_compile_and_verify_cli(tmp_path: Path):
    before = tmp_path/'before.yaml'; after = tmp_path/'after.yaml'; config = tmp_path/'config.yaml'
    before.write_text('a: 1\n---\nb: 2\n', encoding='utf-8')
    after.write_text('b: 2\n---\na: 9\n', encoding='utf-8')
    root = Path(__file__).parents[1]
    cmd = [sys.executable, str(root/'yaml_config_tool.py'), 'compile', str(before), str(after), '-o', str(config)]
    assert subprocess.run(cmd, cwd=root).returncode == 0
    verify = [sys.executable, str(root/'yaml_config_tool.py'), 'verify', str(before), str(config), str(after)]
    assert subprocess.run(verify, cwd=root).returncode == 0


def test_folder_verify_detects_mapping_order_difference(tmp_path: Path):
    a = tmp_path/'a'; b = tmp_path/'b'; gen = tmp_path/'gen'
    (a/'FAB14'/'STAGING').mkdir(parents=True); (b/'FAB14'/'STAGING').mkdir(parents=True)
    (a/'FAB14'/'STAGING'/'x.yaml').write_text('a: 1\nb: 2\n', encoding='utf-8')
    (b/'FAB14'/'STAGING'/'x.yaml').write_text('b: 2\na: 1\n', encoding='utf-8')
    result = FolderCompiler().compile_folder(a, b, gen)
    assert result.verified
    out = tmp_path/'out'; FolderCompiler().apply_manifest(a, gen, out)
    assert strict_documents_equal(load_all(out/'FAB14'/'STAGING'/'x.yaml'), load_all(b/'FAB14'/'STAGING'/'x.yaml'))

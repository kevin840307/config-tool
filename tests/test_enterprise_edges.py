from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from yaml_config_engine.engine import YamlPatchEngine
from yaml_config_engine.errors import MatchError, OperationError, PathError
from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.yamlio import dump_one, load_all, load_one, make_yaml
from xml_config_engine.engine import XmlPatchEngine
from xml_config_engine.folder import XmlFolderEngine
from xml_config_engine.xmltext import XmlFormatError


def y(text: str):
    return make_yaml().load(text)


def test_yaml_set_create_missing_and_replace_root():
    doc = {'a': 1}
    out = YamlPatchEngine().apply_document(doc, {'operations': [
        {'op': 'set', 'path': '$.nested.deep.value', 'value': 2, 'create_missing': True},
        {'op': 'replace', 'path': '$', 'value': {'root': 'replaced'}},
    ]})
    assert out == {'root': 'replaced'}


def test_yaml_remove_escaped_json_pointer_key_and_negative_index():
    doc = {'a/b': {'~key': [1, 2, 3]}}
    out = YamlPatchEngine().apply_document(doc, {'operations': [
        {'op': 'remove', 'path': '$/a~1b/~0key/-1'},
    ]})
    assert out == {'a/b': {'~key': [1, 2]}}


@pytest.mark.parametrize('strategy,expected', [
    ('append', [1, 2, 3]),
    ('prepend', [2, 3, 1]),
    ('unique', [1, 2, 3]),
])
def test_yaml_list_merge_strategies(strategy, expected):
    value = [2, 3] if strategy != 'append' else [2, 3]
    doc = {'items': [1] if strategy != 'unique' else [1, 2]}
    out = YamlPatchEngine().apply_document(doc, {'operations': [
        {'op': 'merge', 'path': '$.items', 'value': value, 'strategy': strategy},
    ]})
    assert out['items'] == expected


def test_yaml_insert_out_of_range_policies_and_negative_index():
    base = {'items': ['A', 'B']}
    out = YamlPatchEngine().apply_document(deepcopy(base), {'operations': [
        {'op': 'insert', 'path': '$.items', 'value': 'X', 'position': {'index': 99, 'on_out_of_range': 'append'}},
        {'op': 'insert', 'path': '$.items', 'value': 'Y', 'position': {'index': -1}},
    ]})
    assert out['items'] == ['A', 'B', 'Y', 'X']
    with pytest.raises(OperationError):
        YamlPatchEngine().apply_document(deepcopy(base), {'operations': [
            {'op': 'insert', 'path': '$.items', 'value': 'X', 'position': {'index': 99}},
        ]})


def test_yaml_duplicate_error_and_multiple_match_policies():
    doc = {'items': [{'id': 'a'}, {'id': 'a'}]}
    with pytest.raises(MatchError):
        YamlPatchEngine().apply_document(deepcopy(doc), {'operations': [
            {'op': 'update_item', 'path': '$.items', 'match': {'id': 'a'}, 'set': {'x': 1}},
        ]})
    out = YamlPatchEngine().apply_document(deepcopy(doc), {'operations': [
        {'op': 'update_item', 'path': '$.items', 'match': {'id': 'a'}, 'set': {'x': 1}, 'on_multiple_matches': 'all'},
    ]})
    assert all(item['x'] == 1 for item in out['items'])
    with pytest.raises(OperationError):
        YamlPatchEngine().apply_document({'items': [{'id': 'a'}]}, {'operations': [
            {'op': 'append', 'path': '$.items', 'value': {'id': 'a'}, 'duplicate': {'unique_by': ['id'], 'policy': 'error'}},
        ]})


def test_yaml_copy_move_conflict_and_key_positions():
    with pytest.raises(OperationError):
        YamlPatchEngine().apply_document({'a': 1, 'b': 2}, {'operations': [
            {'op': 'copy_key', 'path': '$', 'source_key': 'a', 'target_key': 'b'},
        ]})
    out = YamlPatchEngine().apply_document({'a': 1, 'b': 2}, {'operations': [
        {'op': 'copy_key', 'path': '$', 'source_key': 'a', 'target_key': 'b', 'on_conflict': 'replace', 'position': {'first': True}},
    ]})
    assert list(out) == ['b', 'a'] and out['b'] == 1


def test_yaml_document_selector_only_changes_matching_document(tmp_path: Path):
    src = tmp_path / 'multi.yaml'
    src.write_text('kind: A\nvalue: 1\n---\nkind: B\nvalue: 1\n', encoding='utf-8')
    cfg = {'documents': {'match': {'kind': 'B'}}, 'operations': [{'op': 'replace', 'path': '$.value', 'value': 2}]}
    YamlPatchEngine().apply_file(src, cfg)
    docs = load_all(src)
    assert [d['value'] for d in docs] == [1, 2]


def test_yaml_bom_quotes_anchor_comments_and_crlf_combination(tmp_path: Path):
    src = tmp_path / 'config.yaml'
    src.write_bytes(b'\xef\xbb\xbf# top\r\ndefaults: &d\r\n  value: "old" # inline\r\nuse:\r\n  <<: *d\r\n')
    cfg = {'options': {'yaml_output': {'line_ending': 'preserve', 'preserve_quotes': True}},
           'operations': [{'op': 'replace', 'path': '$.defaults.value', 'value': 'new'}]}
    YamlPatchEngine().apply_file(src, cfg)
    payload = src.read_bytes()
    assert payload.startswith(b'\xef\xbb\xbf') and b'\r\n' in payload
    text = payload.decode('utf-8-sig')
    assert '# top' in text and '# inline' in text and '&d' in text and '*d' in text and '"new"' in text


def test_xml_create_missing_attribute_nested_elements_and_self_closing():
    source = '<root><item/></root>'
    out, _ = XmlPatchEngine().apply_text(source, {'operations': [
        {'op': 'set', 'path': '/root/item/@id', 'value': 'a', 'create_missing': True},
        {'op': 'set', 'path': '/root/item/parameters/timeout', 'value': '30', 'create_missing': True},
    ]})
    root = ET.fromstring(out)
    item = root.find('item')
    assert item is not None and item.attrib['id'] == 'a' and item.findtext('./parameters/timeout') == '30'


def test_xml_remove_attribute_and_nested_item_fields():
    source = '<root><items><item id="a" old="x"><nested><x>1</x><y>2</y></nested></item></items></root>'
    out, _ = XmlPatchEngine().apply_text(source, {'operations': [
        {'op': 'update_item', 'path': '/root/items', 'element': 'item', 'match': {'@id': 'a'},
         'set': {'nested.x': '10', 'nested.z': '3'}, 'remove': ['@old', 'nested.y'], 'expect_matches': 1},
    ]})
    item = ET.fromstring(out).find('./items/item')
    assert item is not None and 'old' not in item.attrib
    assert item.findtext('./nested/x') == '10' and item.findtext('./nested/z') == '3' and item.find('./nested/y') is None


def test_xml_insert_before_after_match_exact_order():
    source = '<root><items><item id="a"/><item id="c"/></items></root>'
    out, _ = XmlPatchEngine().apply_text(source, {'operations': [
        {'op': 'insert_before', 'path': '/root/items', 'element': 'item', 'match': {'@id': 'c'},
         'value': {'@attributes': {'id': 'b'}}},
        {'op': 'insert_after', 'path': '/root/items', 'element': 'item', 'match': {'@id': 'c'},
         'value': {'@attributes': {'id': 'd'}}},
    ]})
    assert [x.attrib['id'] for x in ET.fromstring(out).findall('./items/item')] == ['a', 'b', 'c', 'd']


def test_xml_copy_item_duplicate_skip_and_error():
    source = '<root><items><item id="a"><v>1</v></item></items></root>'
    spec = {'op': 'copy_item', 'path': '/root/items', 'element': 'item', 'source': {'match': {'@id': 'a'}},
            'set': {'@id': 'b'}, 'duplicate': {'unique_by': ['@id'], 'policy': 'skip'}, 'position': {'last': True}}
    once, _ = XmlPatchEngine().apply_text(source, {'operations': [spec]})
    twice, _ = XmlPatchEngine().apply_text(once, {'operations': [spec]})
    assert once == twice and [x.attrib['id'] for x in ET.fromstring(once).findall('./items/item')] == ['a', 'b']
    with pytest.raises(XmlFormatError):
        XmlPatchEngine().apply_text(once, {'operations': [{**spec, 'duplicate': {'unique_by': ['@id'], 'policy': 'error'}}]})


def test_xml_move_node_between_parents_and_move_key():
    source = '<root><left><a>1</a><b>2</b></left><right/></root>'
    out, _ = XmlPatchEngine().apply_text(source, {'operations': [
        {'op': 'move_node', 'from_path': '/root/left/a', 'to_path': '/root/right', 'position': {'last': True}},
        {'op': 'move_key', 'path': '/root/left', 'source_key': 'b', 'target_key': 'renamed', 'position': {'first': True}},
    ]})
    root = ET.fromstring(out)
    assert root.find('./left/a') is None and root.findtext('./right/a') == '1' and root.findtext('./left/renamed') == '2'


def test_xml_namespace_cdata_doctype_pi_and_comment_untouched():
    source = '''<?xml version="1.0"?>
<?app keep?>
<!DOCTYPE root [<!ENTITY x "X">]>
<!-- top -->
<root xmlns:n="urn:test"><n:item id="a"><![CDATA[A < B]]></n:item><value>old</value></root>'''
    out, _ = XmlPatchEngine().apply_text(source, {'operations': [
        {'op': 'replace', 'path': '/root/value', 'value': 'new'},
        {'op': 'set', 'path': '/root/n:item/@id', 'value': 'b'},
    ]})
    assert '<?app keep?>' in out and '<!DOCTYPE root' in out and '<!-- top -->' in out and '<![CDATA[A < B]]>' in out
    assert 'n:item id="b"' in out and '<value>new</value>' in out
    ET.fromstring(out)


def test_xml_compiler_mixed_content_uses_exact_fallback():
    from xml_config_engine.compiler import XmlDiffCompiler
    before = '<root><message>Hello <b>Kevin</b>!</message></root>'
    after = '<root><message>Hello <b>Kevin</b>, welcome!</message></root>'
    result = XmlDiffCompiler().compile_text(before, after)
    assert result.verified and result.strategy == 'replace-entire-file-exact'
    assert result.config['xml_exact_text'] == after


def test_yaml_cli_run_folder_and_check_idempotency_with_var(tmp_path: Path):
    project = Path(__file__).resolve().parents[1]
    src = tmp_path / 'src'; target = src / 'FAB14' / 'PROD' / 'app' / 'x.yaml'; target.parent.mkdir(parents=True)
    target.write_text('value: old\n', encoding='utf-8')
    cfg = tmp_path / 'c.yaml'; cfg.write_text('operations:\n  - {op: replace, path: $.value, value: "{{ VALUE }}"}\n', encoding='utf-8')
    out = tmp_path / 'out'
    run = subprocess.run([sys.executable, str(project / 'yaml_config_tool.py'), 'run-folder', str(src), str(cfg), str(out), '--var', 'VALUE=new'], cwd=project, text=True, capture_output=True)
    assert run.returncode == 0, run.stderr + run.stdout
    assert load_one(out / target.relative_to(src))['value'] == 'new'
    idem = subprocess.run([sys.executable, str(project / 'yaml_config_tool.py'), 'check-idempotency', str(src), str(cfg), '--var', 'VALUE=new'], cwd=project, text=True, capture_output=True)
    assert idem.returncode == 0, idem.stderr + idem.stdout


def test_xml_cli_run_folder_and_check_idempotency_with_var(tmp_path: Path):
    project = Path(__file__).resolve().parents[1]
    src = tmp_path / 'src'; target = src / 'FAB14' / 'PROD' / 'app' / 'x.xml'; target.parent.mkdir(parents=True)
    target.write_text('<root><value>old</value></root>', encoding='utf-8')
    cfg = tmp_path / 'c.yaml'; cfg.write_text('operations:\n  - {op: replace, path: /root/value, value: "{{ VALUE }}"}\n', encoding='utf-8')
    out = tmp_path / 'out'
    run = subprocess.run([sys.executable, str(project / 'xml_config_tool.py'), 'run-folder', str(src), str(cfg), str(out), '--var', 'VALUE=new'], cwd=project, text=True, capture_output=True)
    assert run.returncode == 0, run.stderr + run.stdout
    assert ET.fromstring((out / target.relative_to(src)).read_text()).findtext('value') == 'new'
    idem = subprocess.run([sys.executable, str(project / 'xml_config_tool.py'), 'check-idempotency', str(src), str(cfg), '--var', 'VALUE=new'], cwd=project, text=True, capture_output=True)
    assert idem.returncode == 0, idem.stderr + idem.stdout


def test_large_folder_child_rules_scale_and_idempotency(tmp_path: Path):
    source = tmp_path / 'source'
    for fab in ('FAB14-A', 'FAB14-B', 'FAB18-A'):
        for env in ('STAGING', 'PROD'):
            for app_index in range(5):
                p = source / fab / env / f'app-{app_index}' / 'config.yaml'
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f'value: old\napp: {app_index}\n', encoding='utf-8')
    cfg = {
        'operations': [{'op': 'set', 'path': '$.global', 'value': True, 'create_missing': True}],
        'rules': [
            {'id': 'fab14-staging', 'filters': {'fab_allow_prefix': ['FAB14'], 'env_allow': ['STAGING'], 'path_allow': ['app-*/config.yaml']},
             'operations': [{'op': 'replace', 'path': '$.value', 'value': 'matched'}]},
            {'id': 'app3', 'filters': {'path_allow': ['app-3/config.yaml']},
             'operations': [{'op': 'set', 'path': '$.special', 'value': 3, 'create_missing': True}]},
        ],
    }
    out = tmp_path / 'out'
    report = FolderCompiler().apply_rules_config(source, cfg, out)
    assert report['summary']['matched_files'] == 30 and report['summary']['changed_files'] == 30
    assert load_one(out / 'FAB14-A/STAGING/app-1/config.yaml')['value'] == 'matched'
    assert load_one(out / 'FAB18-A/STAGING/app-1/config.yaml')['value'] == 'old'
    assert load_one(out / 'FAB18-A/PROD/app-3/config.yaml')['special'] == 3
    assert FolderCompiler().check_rules_idempotency(source, cfg)['idempotent']


def test_xml_copy_item_to_node_duplicate_guard_is_idempotent():
    source = '<root><items><item id="v1"><value>1</value></item></items><templates/></root>'
    cfg = {'operations': [{
        'op': 'copy_item_to_node',
        'from_path': '/root/items',
        'to_path': '/root/templates',
        'element': 'item',
        'source': {'match': {'@id': 'v1'}, 'expect_matches': 1},
        'set': {'@id': 'template', 'value': 'T'},
        'duplicate': {'unique_by': ['@id'], 'policy': 'skip'},
        'position': {'last': True},
    }]}
    first, _ = XmlPatchEngine().apply_text(source, cfg)
    second, _ = XmlPatchEngine().apply_text(first, cfg)
    assert first == second
    root = ET.fromstring(first)
    templates = root.findall('./templates/item')
    assert len(templates) == 1
    assert templates[0].attrib['id'] == 'template'
    assert templates[0].findtext('value') == 'T'


def test_linter_accepts_optional_unique_remove_without_warning(tmp_path: Path):
    cfg = tmp_path / 'config.yaml'
    cfg.write_text('''operations:\n  - op: remove_item\n    path: $.versions\n    match: {version: old}\n    on_zero_matches: ignore\n''', encoding='utf-8')
    from yaml_config_engine.linting import ConfigLinter
    report = ConfigLinter().lint(cfg)
    assert report.valid
    assert not [x for x in report.warnings if x.code == 'MATCH_WITHOUT_EXPECTATION']

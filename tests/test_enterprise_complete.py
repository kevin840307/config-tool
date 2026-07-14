from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from yaml_config_engine.engine import YamlPatchEngine
from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.yamlio import dump_one, load_one, make_yaml
from xml_config_engine.engine import XmlPatchEngine
from xml_config_engine.folder import XmlFolderEngine
from xml_config_engine.folder_compiler import XmlFolderCompiler


def y(text: str):
    return make_yaml().load(text)


def test_yaml_complete_operation_matrix():
    doc = y('''# keep root comment
settings:
  old_name: old
  keep: yes
  source: &source
    value: 1
    nested:
      enabled: true
  target: {}
items:
  - id: v1
    version: "1.0"
    enabled: true
    obsolete: remove-me
    nested:
      x: 1
  - id: v2
    version: "2.0"
    enabled: false
    nested:
      x: 2
  - id: v3
    version: "3.0"
    enabled: true
queue:
  - A
  - C
''')
    cfg = {
        'variables': {'NEW_VALUE': 'updated'},
        'operations': [
            {'op': 'set', 'path': '$.settings.keep', 'value': '{{ NEW_VALUE }}'},
            {'op': 'replace', 'path': '$.settings.target', 'value': {'replaced': True}},
            {'op': 'merge', 'path': '$.settings', 'value': {'merged': {'a': 1}, 'keep': 'overwritten'}},
            {'op': 'rename_key', 'path': '$.settings', 'old_key': 'old_name', 'new_key': 'new_name'},
            {'op': 'insert_key', 'path': '$.settings', 'key': 'first', 'value': 0, 'position': {'first': True}},
            {'op': 'copy_key', 'path': '$.settings', 'source_key': 'new_name', 'target_key': 'copied_name', 'position': {'after_key': 'new_name'}},
            {'op': 'move_key', 'path': '$.settings', 'source_key': 'merged', 'target_key': 'moved_merged', 'position': {'last': True}},
            {'op': 'copy_node', 'from_path': '$.settings.source', 'to_path': '$.settings.copied_node', 'position': {'after_key': 'source'}},
            {'op': 'move_node', 'from_path': '$.settings.target', 'to_path': '$.settings.moved_target', 'position': {'last': True}},
            {'op': 'append', 'path': '$.queue', 'value': 'D'},
            {'op': 'prepend', 'path': '$.queue', 'value': 'START'},
            {'op': 'insert_before', 'path': '$.items', 'match': {'id': 'v2'}, 'value': {'id': 'v1.5', 'version': '1.5'}, 'expect_matches': 1},
            {'op': 'insert_after', 'path': '$.items', 'match': {'id': 'v2'}, 'value': {'id': 'v2.5', 'version': '2.5'}, 'expect_matches': 1},
            {'op': 'insert_at', 'path': '$.queue', 'index': 2, 'value': 'B'},
            {'op': 'insert', 'path': '$.queue', 'value': 'TAIL', 'position': {'last': True}},
            {'op': 'update_item', 'path': '$.items', 'match': {'id': 'v1'}, 'set': {'version': '1.1', 'nested.x': 11}, 'remove': ['obsolete'], 'expect_matches': 1},
            {'op': 'upsert_item', 'path': '$.items', 'match': {'id': 'v4'}, 'value': {'id': 'v4', 'version': '4.0', 'enabled': True}, 'position': {'last': True}},
            {'op': 'remove_item', 'path': '$.items', 'match': {'id': 'v3'}, 'expect_matches': 1},
            {'op': 'move_item', 'path': '$.items', 'match': {'id': 'v2.5'}, 'position': {'first': True}, 'expect_matches': 1},
            {'op': 'copy_item', 'path': '$.items', 'source': {'match': {'id': 'v2'}, 'expect_matches': 1},
             'set': {'id': 'v2-copy', 'version': '2.1'}, 'remove': ['enabled'],
             'item_operations': [{'op': 'insert_key', 'path': '$', 'key': 'copied', 'value': True, 'position': {'last': True}}],
             'position': {'after': {'match': {'id': 'v2'}, 'expect_matches': 1}}},
            {'op': 'copy_item_to_node', 'from_path': '$.items', 'source': {'match': {'id': 'v1'}, 'expect_matches': 1},
             'to_path': '$.settings.version_template', 'set': {'id': 'template'}, 'position': {'last': True}},
            {'op': 'capture', 'source': 'current', 'path': '$.items', 'match': {'id': 'v1'}, 'as': 'CAPTURED', 'expect_matches': 1},
            {'op': 'set', 'path': '$.settings.captured_version', 'value': '{{ CAPTURED.version }}', 'create_missing': True},
            {'op': 'remove', 'path': '$.settings.copied_name'},
        ],
    }
    out = YamlPatchEngine().apply_document(doc, cfg)
    assert list(out['settings'])[0] == 'first'
    assert out['settings']['keep'] == 'overwritten'
    assert out['settings']['new_name'] == 'old'
    assert 'copied_name' not in out['settings']
    assert out['settings']['copied_node']['nested']['enabled'] is True
    assert 'target' not in out['settings'] and out['settings']['moved_target']['replaced'] is True
    assert out['settings']['captured_version'] == '1.1'
    assert out['settings']['version_template']['id'] == 'template'
    assert out['queue'] == ['START', 'A', 'B', 'C', 'D', 'TAIL']
    ids = [x['id'] for x in out['items']]
    assert ids == ['v2.5', 'v1', 'v1.5', 'v2', 'v2-copy', 'v4']
    v1 = next(x for x in out['items'] if x['id'] == 'v1')
    assert v1['version'] == '1.1' and v1['nested']['x'] == 11 and 'obsolete' not in v1
    copied = next(x for x in out['items'] if x['id'] == 'v2-copy')
    assert copied['version'] == '2.1' and copied['copied'] is True and 'enabled' not in copied


def test_yaml_merge_strategies_and_duplicate_policies():
    base = {'mapping': {'a': 1, 'nested': {'x': 1}}, 'list': [{'id': 'a', 'v': 1}], 'values': [1, 2]}
    cfg = {'operations': [
        {'op': 'merge', 'path': '$.mapping', 'value': {'a': 9, 'b': 2}, 'strategy': 'keep_existing'},
        {'op': 'merge', 'path': '$.mapping', 'value': {'nested': {'x': None, 'y': 2}}, 'strategy': 'delete_null'},
        {'op': 'append', 'path': '$.list', 'value': {'id': 'a', 'v': 2}, 'duplicate': {'unique_by': ['id'], 'policy': 'update'}},
        {'op': 'append', 'path': '$.list', 'value': {'id': 'a', 'v': 3}, 'duplicate': {'unique_by': ['id'], 'policy': 'skip'}},
        {'op': 'merge', 'path': '$.values', 'value': [2, 3], 'strategy': 'unique'},
    ]}
    out = YamlPatchEngine().apply_document(deepcopy(base), cfg)
    assert out['mapping'] == {'a': 1, 'nested': {'y': 2}, 'b': 2}
    assert out['list'] == [{'id': 'a', 'v': 2}]
    assert out['values'] == [1, 2, 3]


def test_xml_complete_operation_matrix():
    source = '''<?xml version="1.0"?>
<!-- keep -->
<root mode='old'>
  <settings>
    <oldName>old</oldName>
    <keep>yes</keep>
    <source><value>1</value></source>
    <target/>
  </settings>
  <items>
    <item id="v1"><version>1.0</version><enabled>true</enabled><obsolete>x</obsolete></item>
    <item id="v2"><version>2.0</version><enabled>false</enabled></item>
    <item id="v3"><version>3.0</version><enabled>true</enabled></item>
  </items>
  <queue><entry>A</entry><entry>C</entry></queue>
  <templates/>
</root>
'''
    cfg = {'operations': [
        {'op': 'set', 'path': '/root/@mode', 'value': 'new'},
        {'op': 'replace', 'path': '/root/settings/keep', 'value': 'updated'},
        {'op': 'merge', 'path': '/root/settings', 'value': {'merged': {'a': '1'}, 'keep': 'overwritten'}},
        {'op': 'rename_key', 'path': '/root/settings', 'old_key': 'oldName', 'new_key': 'newName'},
        {'op': 'insert_key', 'path': '/root/settings', 'key': 'first', 'value': '0', 'position': {'first': True}},
        {'op': 'copy_key', 'path': '/root/settings', 'source_key': 'newName', 'target_key': 'copiedName', 'position': {'after_key': 'newName'}},
        {'op': 'move_key', 'path': '/root/settings', 'source_key': 'merged', 'target_key': 'movedMerged', 'position': {'last': True}},
        {'op': 'copy_node', 'from_path': '/root/settings/source', 'to_path': '/root/settings', 'position': {'last': True}},
        {'op': 'append', 'path': '/root/queue', 'element': 'entry', 'value': 'D'},
        {'op': 'prepend', 'path': '/root/queue', 'element': 'entry', 'value': 'START'},
        {'op': 'insert_before', 'path': '/root/items', 'element': 'item', 'match': {'@id': 'v2'},
         'value': {'@attributes': {'id': 'v1.5'}, 'version': '1.5'}},
        {'op': 'insert_after', 'path': '/root/items', 'element': 'item', 'match': {'@id': 'v2'},
         'value': {'@attributes': {'id': 'v2.5'}, 'version': '2.5'}},
        {'op': 'insert_at', 'path': '/root/queue', 'element': 'entry', 'index': 2, 'value': 'B'},
        {'op': 'insert', 'path': '/root/queue', 'element': 'entry', 'position': {'last': True}, 'value': 'TAIL'},
        {'op': 'update_item', 'path': '/root/items', 'element': 'item', 'match': {'@id': 'v1'},
         'set': {'version': '1.1', '@status': 'active', 'newParam': 'N'}, 'remove': ['obsolete'], 'expect_matches': 1},
        {'op': 'upsert_item', 'path': '/root/items', 'element': 'item', 'match': {'@id': 'v4'},
         'value': {'@attributes': {'id': 'v4'}, 'version': '4.0', 'enabled': 'true'}, 'position': {'last': True}},
        {'op': 'remove_item', 'path': '/root/items', 'element': 'item', 'match': {'@id': 'v3'}, 'expect_matches': 1},
        {'op': 'move_item', 'path': '/root/items', 'element': 'item', 'source': {'match': {'@id': 'v2.5'}}, 'position': {'first': True}},
        {'op': 'copy_item', 'path': '/root/items', 'element': 'item', 'source': {'match': {'@id': 'v2'}},
         'set': {'@id': 'v2-copy', 'version': '2.1', 'copied': 'true'}, 'remove': ['enabled'],
         'duplicate': {'unique_by': ['@id'], 'policy': 'skip'}, 'position': {'after': {'match': {'@id': 'v2'}}}},
        {'op': 'copy_item_to_node', 'from_path': '/root/items', 'to_path': '/root/templates', 'element': 'item',
         'source': {'match': {'@id': 'v1'}, 'expect_matches': 1},
         'set': {'@id': 'template', 'version': 'T'}, 'remove': ['enabled'],
         'duplicate': {'unique_by': ['@id'], 'policy': 'skip'}, 'position': {'last': True}},
        {'op': 'capture', 'path': '/root/items/item[@id="v1"]/version', 'as': 'OLD_VERSION'},
        {'op': 'insert_key', 'path': '/root/settings', 'key': 'capturedVersion', 'value': '{{ OLD_VERSION }}', 'position': {'last': True}},
        {'op': 'remove', 'path': '/root/settings/copiedName'},
    ]}
    out, _ = XmlPatchEngine().apply_text(source, cfg)
    ET.fromstring(out)
    assert '<!-- keep -->' in out
    assert "mode='new'" in out
    assert '<keep>overwritten</keep>' in out
    assert '<newName>old</newName>' in out and '<copiedName>' not in out
    root = ET.fromstring(out)
    items = root.find('items')
    assert items is not None
    ids = [i.attrib['id'] for i in items.findall('item')]
    assert ids == ['v2.5', 'v1', 'v1.5', 'v2', 'v2-copy', 'v4']
    v1 = next(i for i in items.findall('item') if i.attrib['id'] == 'v1')
    assert v1.attrib['status'] == 'active'
    assert v1.findtext('version') == '1.1' and v1.find('obsolete') is None and v1.findtext('newParam') == 'N'
    copied = next(i for i in items.findall('item') if i.attrib['id'] == 'v2-copy')
    assert copied.findtext('version') == '2.1' and copied.find('enabled') is None and copied.findtext('copied') == 'true'
    assert root.findtext('./settings/capturedVersion') == '1.1'
    template = root.find('./templates/item')
    assert template is not None and template.attrib['id'] == 'template'
    assert template.findtext('./version') == 'T' and template.find('./enabled') is None
    assert [x.text for x in root.findall('./queue/entry')] == ['START', 'A', 'B', 'C', 'D', 'TAIL']


def _yaml_multiversion_source() -> str:
    return '''# application release matrix
application:
  name: demo
  audit: keep-this-comment
versions:
  # oldest version should be removed
  - version: "2025.10"
    status: deprecated
    parameters:
      timeout: 10
      legacyMode: true
  # latest existing version is the clone source
  - version: "2026.01"
    status: active
    parameters:
      timeout: 20
      retry: 2
    sections:
      logging:
        level: INFO
'''


def _yaml_multiversion_config() -> dict:
    return {
        'variable_map': {
            'FAB14': {'NEW_VERSION': '2026.07', 'OLD_TIMEOUT': 45},
            'FAB14:STAGING': {'NEW_RETRY': 5, 'NEW_FEATURE': 'staging-feature'},
        },
        'operations': [
            {'op': 'remove_item', 'path': '$.versions', 'match': {'version': '2025.10'}, 'on_zero_matches': 'ignore', 'remove_leading_comments': True},
            {'op': 'copy_item', 'path': '$.versions', 'source': {'match': {'version': '2026.01'}, 'expect_matches': 1},
             'set': {'version': '{{ NEW_VERSION }}', 'status': 'candidate', 'parameters.retry': '{{ NEW_RETRY }}'},
             'item_operations': [
                 {'op': 'insert_key', 'path': '$.parameters', 'key': 'newParameter', 'value': '{{ NEW_FEATURE }}', 'position': {'last': True}},
                 {'op': 'insert_key', 'path': '$.sections', 'key': 'featureFlags', 'value': {'enabled': True, 'mode': 'safe'}, 'position': {'last': True}},
             ],
             'duplicate': {'unique_by': ['version'], 'policy': 'skip'},
             'copy_leading_comments': False,
             'position': {'after': {'match': {'version': '2026.01'}, 'expect_matches': 1}}},
            {'op': 'update_item', 'path': '$.versions', 'match': {'version': '2026.01'},
             'set': {'parameters.timeout': '{{ OLD_TIMEOUT }}', 'parameters.compatibility': 'legacy-compatible'},
             'expect_matches': 1},
        ],
    }


def test_yaml_multiversion_upgrade_rules_folder_and_idempotency(tmp_path: Path):
    source = tmp_path / 'source'
    target = source / 'FAB14-FZ1' / 'STAGING' / 'app' / 'application.yaml'
    target.parent.mkdir(parents=True)
    target.write_text(_yaml_multiversion_source(), encoding='utf-8')
    config = tmp_path / 'config.yaml'
    dump_one(_yaml_multiversion_config(), config)

    out = tmp_path / 'out'
    report = FolderCompiler().apply_rules_config(source, config, out)
    result = load_one(out / target.relative_to(source))
    versions = result['versions']
    assert [x['version'] for x in versions] == ['2026.01', '2026.07']
    old, new = versions
    assert old['parameters']['timeout'] == 45
    assert old['parameters']['compatibility'] == 'legacy-compatible'
    assert new['status'] == 'candidate'
    assert new['parameters']['retry'] == 5
    assert new['parameters']['timeout'] == 20
    assert 'compatibility' not in new['parameters']
    assert new['parameters']['newParameter'] == 'staging-feature'
    assert new['sections']['featureFlags'] == {'enabled': True, 'mode': 'safe'}
    text = (out / target.relative_to(source)).read_text(encoding='utf-8')
    assert '# application release matrix' in text
    assert '# oldest version should be removed' not in text
    assert text.count('# latest existing version is the clone source') == 1
    assert report['summary']['changed_files'] == 1

    idem = FolderCompiler().check_rules_idempotency(source, config)
    assert idem['idempotent'], idem


def _xml_multiversion_source() -> str:
    return '''<?xml version="1.0"?>
<!-- release matrix -->
<application>
  <versions>
    <!-- oldest version should be removed -->
    <version id="2025.10" status="deprecated">
      <parameters><timeout>10</timeout><legacyMode>true</legacyMode></parameters>
    </version>
    <!-- latest existing version is clone source -->
    <version id="2026.01" status="active">
      <parameters><timeout>20</timeout><retry>2</retry></parameters>
      <sections><logging><level>INFO</level></logging></sections>
    </version>
  </versions>
</application>
'''


def _xml_multiversion_config() -> dict:
    return {
        'variable_map': {
            'FAB14': {'NEW_VERSION': '2026.07', 'OLD_TIMEOUT': 45},
            'FAB14:STAGING': {'NEW_RETRY': 5, 'NEW_FEATURE': 'staging-feature'},
        },
        'operations': [
            {'op': 'remove_item', 'path': '/application/versions', 'element': 'version', 'match': {'@id': '2025.10'}, 'on_zero_matches': 'ignore', 'remove_leading_comments': True},
            {'op': 'copy_item', 'path': '/application/versions', 'element': 'version',
             'source': {'match': {'@id': '2026.01'}, 'expect_matches': 1},
             'set': {'@id': '{{ NEW_VERSION }}', '@status': 'candidate', 'parameters.retry': '{{ NEW_RETRY }}',
                     'parameters.newParameter': '{{ NEW_FEATURE }}', 'sections.featureFlags.enabled': 'true', 'sections.featureFlags.mode': 'safe'},
             'duplicate': {'unique_by': ['@id'], 'policy': 'skip'},
             'position': {'after': {'match': {'@id': '2026.01'}}}},
            {'op': 'update_item', 'path': '/application/versions', 'element': 'version', 'match': {'@id': '2026.01'},
             'set': {'parameters.timeout': '{{ OLD_TIMEOUT }}', 'parameters.compatibility': 'legacy-compatible'}, 'expect_matches': 1},
        ],
    }


def test_xml_multiversion_upgrade_rules_folder_and_idempotency(tmp_path: Path):
    source = tmp_path / 'source'
    target = source / 'FAB14-FZ1' / 'STAGING' / 'app' / 'application.xml'
    target.parent.mkdir(parents=True)
    target.write_text(_xml_multiversion_source(), encoding='utf-8')
    config = tmp_path / 'config.yaml'
    dump_one(_xml_multiversion_config(), config)

    out = tmp_path / 'out'
    report = XmlFolderEngine().apply_rules(source, config, out)
    result_path = out / target.relative_to(source)
    root = ET.fromstring(result_path.read_text(encoding='utf-8'))
    versions = root.findall('./versions/version')
    assert [x.attrib['id'] for x in versions] == ['2026.01', '2026.07']
    old, new = versions
    assert old.findtext('./parameters/timeout') == '45'
    assert old.findtext('./parameters/compatibility') == 'legacy-compatible'
    assert new.attrib['status'] == 'candidate'
    assert new.findtext('./parameters/retry') == '5'
    assert new.findtext('./parameters/timeout') == '20'
    assert new.find('./parameters/compatibility') is None
    assert new.findtext('./parameters/newParameter') == 'staging-feature'
    assert new.findtext('./sections/featureFlags/enabled') == 'true'
    xml_text = result_path.read_text(encoding='utf-8')
    assert '<!-- release matrix -->' in xml_text
    assert '<!-- oldest version should be removed -->' not in xml_text
    assert xml_text.count('<!-- latest existing version is clone source -->') == 1
    assert report['summary']['changed'] == 1

    first = tmp_path / 'first'
    second = tmp_path / 'second'
    XmlFolderEngine().apply_rules(source, config, first)
    XmlFolderEngine().apply_rules(first, config, second)
    assert (first / target.relative_to(source)).read_bytes() == (second / target.relative_to(source)).read_bytes()


def test_compile_folder_complex_multiversion_yaml_and_xml(tmp_path: Path):
    # YAML exact structural reproduction with create/delete/patch.
    y_before = tmp_path / 'yb'; y_after = tmp_path / 'ya'; y_gen = tmp_path / 'yg'; y_out = tmp_path / 'yo'
    (y_before / 'child').mkdir(parents=True); (y_after / 'child').mkdir(parents=True)
    (y_before / 'child/app.yaml').write_text(_yaml_multiversion_source(), encoding='utf-8')
    cfg = _yaml_multiversion_config()
    upgraded = YamlPatchEngine().apply_document(load_one(y_before / 'child/app.yaml'), cfg, {
        'NEW_VERSION': '2026.07', 'OLD_TIMEOUT': 45, 'NEW_RETRY': 5, 'NEW_FEATURE': 'staging-feature'
    })
    dump_one(upgraded, y_after / 'child/app.yaml')
    (y_before / 'child/delete.yaml').write_text('old: true\n', encoding='utf-8')
    (y_after / 'child/create.yaml').write_text('new: true\n', encoding='utf-8')
    compiled = FolderCompiler().compile_folder(y_before, y_after, y_gen)
    assert compiled.verified
    FolderCompiler().apply_manifest(y_before, y_gen, y_out)
    assert FolderCompiler().verify_manifest(y_before, y_gen, y_after)

    # XML exact tree reproduction including comments/format fallback when needed.
    x_before = tmp_path / 'xb'; x_after = tmp_path / 'xa'; x_gen = tmp_path / 'xg'; x_out = tmp_path / 'xo'
    (x_before / 'child').mkdir(parents=True); (x_after / 'child').mkdir(parents=True)
    (x_before / 'child/app.xml').write_text(_xml_multiversion_source(), encoding='utf-8')
    xml_upgraded, _ = XmlPatchEngine().apply_text(_xml_multiversion_source(), _xml_multiversion_config(), {
        'NEW_VERSION': '2026.07', 'OLD_TIMEOUT': 45, 'NEW_RETRY': 5, 'NEW_FEATURE': 'staging-feature'
    })
    (x_after / 'child/app.xml').write_text(xml_upgraded, encoding='utf-8')
    (x_before / 'child/delete.xml').write_text('<old/>\n', encoding='utf-8')
    (x_after / 'child/create.xml').write_text('<new/>\n', encoding='utf-8')
    xcompiled = XmlFolderCompiler().compile_folder(x_before, x_after, x_gen)
    assert xcompiled.verified
    XmlFolderCompiler().apply_folder(x_before, x_gen, x_out)
    assert {p.relative_to(x_out): p.read_bytes() for p in x_out.rglob('*') if p.is_file()} == {
        p.relative_to(x_after): p.read_bytes() for p in x_after.rglob('*') if p.is_file()
    }


def test_child_path_global_and_specific_rules_with_variables(tmp_path: Path):
    source = tmp_path / 'source'
    for rel in ['common.yaml', 'child-a/app.yaml', 'child-a/deep/db.yaml', 'child-b/app.yaml']:
        p = source / rel; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('common: old\nchild: none\n', encoding='utf-8')
    cfg = {
        'operations': [{'op': 'set', 'path': '$.common', 'value': 'global'}],
        'rules': [
            {'id': 'child-a', 'priority': 100, 'filters': {'path_allow': ['child-a/**']},
             'operations': [{'op': 'set', 'path': '$.child', 'value': 'A'}]},
            {'id': 'child-a-deep', 'priority': 200, 'filters': {'path_allow': ['child-a/deep/**']},
             'operations': [{'op': 'set', 'path': '$.child', 'value': 'A-DEEP'}]},
            {'id': 'child-b-file', 'filters': {'path_allow': ['child-b/app.yaml']},
             'operations': [{'op': 'set', 'path': '$.child', 'value': 'B'}]},
        ]
    }
    out = tmp_path / 'out'
    FolderCompiler().apply_rules_config(source, cfg, out)
    assert load_one(out / 'common.yaml') == {'common': 'global', 'child': 'none'}
    assert load_one(out / 'child-a/app.yaml')['child'] == 'A'
    # Higher priority runs first and lower priority runs later, so cumulative override is deterministic.
    assert load_one(out / 'child-a/deep/db.yaml')['child'] == 'A'
    assert load_one(out / 'child-b/app.yaml')['child'] == 'B'


def test_linux_wrappers_smoke(tmp_path: Path):
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / 'source.yaml'; config = tmp_path / 'config.yaml'; out = tmp_path / 'out.yaml'
    source.write_text('a: 1\n', encoding='utf-8')
    config.write_text('operations:\n  - {op: replace, path: $.a, value: 2}\n', encoding='utf-8')
    result = subprocess.run([str(project / 'RUN_LINUX.sh'), 'apply', str(source), str(config), '-o', str(out)],
                            cwd=project, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert load_one(out)['a'] == 2


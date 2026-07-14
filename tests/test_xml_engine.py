from pathlib import Path
import xml.etree.ElementTree as ET

from xml_config_engine.engine import XmlPatchEngine
from xml_config_engine.cli import main


def cfg(*ops, **extra):
    return {'version':1,'options':{'atomic_write':True},'operations':list(ops),**extra}


def test_set_text_preserves_comments_spacing_attribute_order_and_crlf():
    source = '<?xml version="1.0"?>\r\n<!-- top -->\r\n<root b="2" a="1">\r\n  <!-- keep -->\r\n  <value>old</value>\r\n</root>\r\n'
    out,_ = XmlPatchEngine().apply_text(source,cfg({'op':'set','path':'/root/value','value':'new'}))
    assert out == source.replace('<value>old</value>','<value>new</value>')


def test_set_attribute_preserves_quote_style_and_order():
    source = "<root><item z='9' name='old' x=\"1\"/></root>"
    out,_ = XmlPatchEngine().apply_text(source,cfg({'op':'set','path':"/root/item/@name",'value':'A&B'}))
    assert out == "<root><item z='9' name='A&amp;B' x=\"1\"/></root>"


def test_xpath_attribute_predicate():
    source = '<root>\n  <item name="a">1</item>\n  <item name="b">2</item>\n</root>\n'
    out,_ = XmlPatchEngine().apply_text(source,cfg({'op':'set','path':"/root/item[@name='b']",'value':'20'}))
    assert '<item name="a">1</item>' in out
    assert '<item name="b">20</item>' in out


def test_remove_element_keeps_neighbor_format():
    source = '<root>\n  <!-- c -->\n  <a>1</a>\n  <b>2</b>\n</root>\n'
    out,_ = XmlPatchEngine().apply_text(source,cfg({'op':'remove','path':'/root/a'}))
    assert out == '<root>\n  <!-- c -->\n  <b>2</b>\n</root>\n'


def test_rename_child_only_changes_tag_names():
    source = '<root>\n  <old x="1"><!--x--><v>2</v></old>\n</root>\n'
    out,_ = XmlPatchEngine().apply_text(source,cfg({'op':'rename_key','path':'/root','old_key':'old','new_key':'new'}))
    assert out == '<root>\n  <new x="1"><!--x--><v>2</v></new>\n</root>\n'


def test_insert_key_preserves_existing_content():
    source = '<root>\n  <!-- keep -->\n  <a>1</a>\n</root>\n'
    out,_ = XmlPatchEngine().apply_text(source,cfg({'op':'insert_key','path':'/root','key':'b','value':'2','position':{'after_key':'a'}}))
    assert '<!-- keep -->' in out
    assert '  <a>1</a>\n  <b>2</b>' in out
    ET.fromstring(out)


def test_merge_updates_existing_and_adds_missing():
    source = '<root>\n  <a>1</a>\n</root>\n'
    out,_ = XmlPatchEngine().apply_text(source,cfg({'op':'merge','path':'/root','value':{'a':'10','b':'20'}}))
    assert '<a>10</a>' in out and '<b>20</b>' in out
    ET.fromstring(out)


def test_capture_and_template():
    source = '<root><a>OLD</a><b>x</b></root>'
    out,_ = XmlPatchEngine().apply_text(source,cfg(
        {'op':'capture','path':'/root/a','as':'OLD'},
        {'op':'set','path':'/root/b','value':'{{ OLD }}-new'},
    ))
    assert out == '<root><a>OLD</a><b>OLD-new</b></root>'


def test_create_missing_simple_path():
    source = '<root>\n</root>\n'
    out,_ = XmlPatchEngine().apply_text(source,cfg({'op':'set','path':'/root/a','value':'1','create_missing':True}))
    assert '<a>1</a>' in out
    ET.fromstring(out)


def test_apply_file_keeps_utf8_bom(tmp_path: Path):
    src=tmp_path/'x.xml'; src.write_bytes(b'\xef\xbb\xbf<root><a>1</a></root>')
    out=tmp_path/'o.xml'
    XmlPatchEngine().apply_file(src,cfg({'op':'set','path':'/root/a','value':'2'}),out)
    assert out.read_bytes().startswith(b'\xef\xbb\xbf')


def test_compile_and_verify_exact_text(tmp_path: Path):
    before=tmp_path/'before.xml'; after=tmp_path/'after.xml'; config=tmp_path/'patch.yaml'; output=tmp_path/'out.xml'
    before.write_text('<root>\n  <a>1</a>\n</root>\n',encoding='utf-8')
    after.write_text('<?xml version="1.0"?>\n<!--x-->\n<root><a>2</a></root>\n',encoding='utf-8')
    assert main(['compile',str(before),str(after),'-o',str(config)]) == 0
    assert main(['apply',str(before),str(config),'-o',str(output)]) == 0
    assert output.read_bytes() == after.read_bytes()
    assert main(['verify',str(before),str(config),str(after)]) == 0


def test_folder_rules_variable_map_and_yaml_is_not_included(tmp_path: Path):
    src=tmp_path/'src'; target=src/'FAB14-FZ1'/'STAGING'/'app'/'x.xml'; target.parent.mkdir(parents=True)
    target.write_text('<root>\n  <!-- keep -->\n  <value>old</value>\n</root>\n',encoding='utf-8')
    y=target.with_name('x.yaml'); y.write_text('value: old\n',encoding='utf-8')
    config=tmp_path/'config.yaml'; config.write_text('''variable_map:\n  FAB14: {NEW: A}\n  FAB14-FZ1: {NEW: B}\n  FAB14-FZ1:STAGING: {NEW: C}\nrules:\n  - id: update\n    filters:\n      path_allow: [app/x.xml]\n    operations:\n      - op: set\n        path: /root/value\n        value: "{{ NEW }}"\n''',encoding='utf-8')
    out=tmp_path/'out'
    assert main(['apply-rules-folder',str(src),str(config),str(out)]) == 0
    assert '<value>C</value>' in (out/'FAB14-FZ1'/'STAGING'/'app'/'x.xml').read_text()
    assert (out/'FAB14-FZ1'/'STAGING'/'app'/'x.yaml').read_text() == 'value: old\n'


def test_xml_idempotent_set(tmp_path: Path):
    src=tmp_path/'src'; p=src/'FAB14'/'PROD'/'app'/'x.xml'; p.parent.mkdir(parents=True); p.write_text('<r><v>1</v></r>')
    config=tmp_path/'c.yaml'; config.write_text('''rules:\n- id: x\n  filters: {path_allow: [app/x.xml]}\n  operations:\n  - {op: set, path: /r/v, value: 2}\n''')
    out=tmp_path/'out'
    assert main(['run-folder',str(src),str(config),str(out)]) == 0


def test_yaml_regression_runner_still_imports_original_engine():
    from yaml_config_engine.engine import YamlPatchEngine
    assert YamlPatchEngine.__module__ == 'yaml_config_engine.engine'


def test_update_and_remove_item_by_attribute_match():
    source = '<root>\n  <items>\n    <item id="a"><value>1</value></item>\n    <item id="b"><value>2</value></item>\n  </items>\n</root>\n'
    config = cfg(
        {'op':'update_item','path':'/root/items','element':'item','match':{'@id':'b'},'set':{'value':'20'}},
        {'op':'remove_item','path':'/root/items','element':'item','match':{'@id':'a'}},
    )
    out,_=XmlPatchEngine().apply_text(source,config)
    assert 'id="a"' not in out
    assert '<item id="b"><value>20</value></item>' in out
    ET.fromstring(out)


def test_copy_item_keeps_original_item_text():
    source = '<root>\n  <items>\n    <item id="a"><!--c--><v>1</v></item>\n  </items>\n</root>\n'
    out,_=XmlPatchEngine().apply_text(source,cfg({'op':'copy_item','path':'/root/items','element':'item','source':{'match':{'@id':'a'}},'position':{'last':True}}))
    assert out.count('<!--c-->') == 2
    assert out.count('<item id="a">') == 2
    ET.fromstring(out)


def test_xml_output_line_ending_crlf(tmp_path):
    src = tmp_path / 'in.xml'
    out = tmp_path / 'out.xml'
    src.write_bytes(b'<root>\n  <value>old</value>\n</root>\n')
    config = {
        'version': 1,
        'format': 'xml',
        'options': {'xml_output': {'line_ending': 'crlf'}},
        'operations': [{'op': 'set', 'path': '/root/value', 'value': 'new'}],
    }
    XmlPatchEngine().apply_file(src, config, out)
    payload = out.read_bytes()
    assert b'\r\n' in payload
    assert b'\n' not in payload.replace(b'\r\n', b'')


def test_xml_compile_folder_default_outputs_only_patch_and_applies(tmp_path):
    from xml_config_engine.folder_compiler import XmlFolderCompiler
    before = tmp_path / 'before'; after = tmp_path / 'after'; generated = tmp_path / 'generated'; output = tmp_path / 'output'
    before.mkdir(); after.mkdir()
    (before / 'app.xml').write_text('<root><port>8080</port></root>\n', encoding='utf-8')
    (after / 'app.xml').write_text('<root><port>9090</port></root>\n', encoding='utf-8')
    result = XmlFolderCompiler().compile_folder(before, after, generated)
    assert result.verified
    assert sorted(p.name for p in generated.iterdir()) == ['patch.yaml']
    XmlFolderCompiler().apply_folder(before, generated, output)
    assert '<port>9090</port>' in (output / 'app.xml').read_text(encoding='utf-8')


def test_xml_compact_patch_supports_external_variable_map(tmp_path):
    from xml_config_engine.folder_compiler import XmlFolderCompiler
    before = tmp_path / 'before'; generated = tmp_path / 'generated'; output = tmp_path / 'output'
    target = before / 'FAB14-FZ1' / 'STAGING' / 'app' / 'application.xml'
    target.parent.mkdir(parents=True)
    target.write_text('<configuration><server host="old"/></configuration>\n', encoding='utf-8')
    generated.mkdir()
    (generated / 'variable-map.yaml').write_text('FAB14:STAGING:\n  HOST: staging-xml-host\n', encoding='utf-8')
    (generated / 'patch.yaml').write_text(
        '''version: 1
kind: xml-folder-patch-compact
variable_map_file: variable-map.yaml
files:
  FAB14-FZ1/STAGING/app/application.xml:
    config:
      version: 1
      format: xml
      operations:
        - op: set
          path: /configuration/server/@host
          value: "{{ HOST }}"
summary: {patch: 1, create: 0, delete: 0, unchanged: 0}
''', encoding='utf-8')
    XmlFolderCompiler().apply_folder(before, generated, output)
    assert 'host="staging-xml-host"' in (output / 'FAB14-FZ1' / 'STAGING' / 'app' / 'application.xml').read_text(encoding='utf-8')

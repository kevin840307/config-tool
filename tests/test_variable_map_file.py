from pathlib import Path

from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.engine import YamlPatchEngine
from xml_config_engine.folder import XmlFolderEngine


def test_yaml_external_variable_map_and_inline_override(tmp_path: Path):
    source = tmp_path/'source'; output = tmp_path/'output'
    target = source/'FAB14'/'STAGING'/'app.yaml'
    target.parent.mkdir(parents=True); target.write_text('value: old\n', encoding='utf-8')
    (tmp_path/'variable-map.yaml').write_text('''
FAB14:
  VALUE: generic
FAB14:STAGING:
  VALUE: external-staging
''', encoding='utf-8')
    config = tmp_path/'config.yaml'
    config.write_text('''
variable_map_file: variable-map.yaml
variable_map:
  FAB14:STAGING:
    VALUE: inline-staging
rules:
  - id: update
    operations:
      - op: set
        path: value
        value: "{{ VALUE }}"
''', encoding='utf-8')
    FolderCompiler().apply_rules_config(source, config, output)
    assert (output/'FAB14'/'STAGING'/'app.yaml').read_text(encoding='utf-8') == 'value: inline-staging\n'


def test_yaml_single_file_external_variable_map(tmp_path: Path):
    source=tmp_path/'a.yaml'; source.write_text('value: old\n', encoding='utf-8')
    (tmp_path/'vars.yaml').write_text('variable_map:\n  FAB14:STAGING: {VALUE: hello}\n', encoding='utf-8')
    config=tmp_path/'config.yaml'; config.write_text('''
variable_map_file: vars.yaml
variables: {VALUE: default}
operations:
  - op: set
    path: value
    value: "{{ VALUE }}"
''', encoding='utf-8')
    # Single-file apply has no FAB/ENV scope resolution by design; explicit variables still work.
    cfg=YamlPatchEngine().load_config(config)
    assert cfg.variable_map['FAB14:STAGING']['VALUE'] == 'hello'


def test_xml_external_variable_map_and_cli_override(tmp_path: Path):
    source=tmp_path/'source'; output=tmp_path/'output'
    target=source/'FAB14'/'STAGING'/'app.xml'
    target.parent.mkdir(parents=True); target.write_text('<root><value>old</value></root>\n', encoding='utf-8')
    (tmp_path/'vars.yaml').write_text('variable_map:\n  FAB14:STAGING: {VALUE: external}\n', encoding='utf-8')
    config=tmp_path/'config.yaml'; config.write_text('''
variable_map_file: vars.yaml
rules:
  - id: update
    operations:
      - op: set
        path: /root/value
        value: "{{ VALUE }}"
''', encoding='utf-8')
    XmlFolderEngine().apply_rules(source, config, output, {'VALUE':'cli'})
    assert '<value>cli</value>' in (output/'FAB14'/'STAGING'/'app.xml').read_text(encoding='utf-8')


def test_rule_level_variable_map_file(tmp_path: Path):
    source=tmp_path/'source'; output=tmp_path/'output'
    target=source/'FAB14'/'STAGING'/'app.yaml'
    target.parent.mkdir(parents=True); target.write_text('value: old\n', encoding='utf-8')
    (tmp_path/'rule-vars.yaml').write_text('FAB14:STAGING: {VALUE: rule-file}\n', encoding='utf-8')
    config=tmp_path/'config.yaml'; config.write_text('''
variable_map:
  FAB14:STAGING: {VALUE: global}
rules:
  - id: update
    variable_map_file: rule-vars.yaml
    operations:
      - op: set
        path: value
        value: "{{ VALUE }}"
''', encoding='utf-8')
    FolderCompiler().apply_rules_config(source, config, output)
    assert (output/'FAB14'/'STAGING'/'app.yaml').read_text(encoding='utf-8') == 'value: rule-file\n'

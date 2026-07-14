from copy import deepcopy
from pathlib import Path
from ruamel.yaml.comments import CommentedMap
from yaml_config_engine import YamlPatchEngine, DiffCompiler
from yaml_config_engine.yamlio import make_yaml, dumps, load_one
from yaml_config_engine.discovery import discover


def parse(text):
    return make_yaml().load(text)


def test_copy_item_after_with_variables_and_deep_copy():
    doc = parse('''db:\n  - version: "2025.6"\n    data: "B"\n    nested:\n      x: 1\n''')
    cfg = {"version": 1, "variables": {"SRC": "2025.6", "DST": "2025.4"}, "operations": [{
        "op": "copy_item", "path": "$.db", "source": {"match": {"version": "{{ SRC }}"}, "expect_matches": 1},
        "set": {"version": "{{ DST }}"}, "position": {"after": {"match": {"version": "{{ SRC }}"}, "expect_matches": 1}}
    }]}
    out = YamlPatchEngine().apply_document(doc, cfg)
    assert [x["version"] for x in out["db"]] == ["2025.6", "2025.4"]
    out["db"][1]["nested"]["x"] = 9
    assert out["db"][0]["nested"]["x"] == 1


def test_insert_before_after_and_index():
    doc = parse('''db:\n  - name: A\n  - name: C\n''')
    cfg = {"version": 1, "operations": [
        {"op": "insert_before", "path": "$.db", "match": {"name": "C"}, "value": {"name": "B"}, "expect_matches": 1},
        {"op": "insert_at", "path": "$.db", "index": 0, "value": {"name": "Z"}},
        {"op": "insert_after", "path": "$.db", "match": {"name": "C"}, "value": {"name": "D"}, "expect_matches": 1},
    ]}
    out = YamlPatchEngine().apply_document(doc, cfg)
    assert [x["name"] for x in out["db"]] == ["Z", "A", "B", "C", "D"]


def test_mapping_key_position_and_rename_preserve_order():
    doc = parse('''database:\n  host: localhost\n  port: 3306\n''')
    cfg = {"version": 1, "operations": [
        {"op": "insert_key", "path": "$.database", "key": "timeout", "value": 30, "position": {"after_key": "host"}},
        {"op": "rename_key", "path": "$.database", "old_key": "port", "new_key": "db_port"},
    ]}
    out = YamlPatchEngine().apply_document(doc, cfg)
    assert list(out["database"].keys()) == ["host", "timeout", "db_port"]


def test_comments_quotes_and_unmodified_format_are_preserved(tmp_path):
    src = tmp_path / "a.yaml"
    src.write_text('# top\ndb:\n  # release\n  - version: "2025.6"\n    data: \'B\' # inline\nother: "keep"\n', encoding='utf-8')
    cfg = {"version": 1, "operations": [{"op": "replace", "path": "$.db[0].version", "value": "2026.4"}]}
    YamlPatchEngine().apply_file(src, cfg)
    text = src.read_text(encoding='utf-8')
    assert '# top' in text and '# release' in text and '# inline' in text
    assert "data: 'B'" in text and 'other: "keep"' in text
    assert 'version: "2026.4"' in text


def test_anchor_alias_survive_round_trip(tmp_path):
    src = tmp_path / "a.yaml"
    src.write_text('defaults: &defaults\n  timeout: 30\nservice:\n  <<: *defaults\n', encoding='utf-8')
    cfg = {"version": 1, "operations": [{"op": "replace", "path": "$.defaults.timeout", "value": 60}]}
    YamlPatchEngine().apply_file(src, cfg)
    text = src.read_text(encoding='utf-8')
    assert '&defaults' in text and '*defaults' in text and 'timeout: 60' in text


def test_multi_document_file(tmp_path):
    src = tmp_path / "multi.yaml"
    src.write_text('a: 1\n---\na: 1\n', encoding='utf-8')
    cfg = {"version": 1, "operations": [{"op": "replace", "path": "$.a", "value": 2}]}
    result = YamlPatchEngine().apply_file(src, cfg)
    assert [d['a'] for d in result.documents] == [2, 2]


def test_diff_compiler_copy_clone_and_verify():
    a = parse('''db:\n  - version: "2025.6"\n    data: B\n''')
    b = parse('''db:\n  - version: "2025.6"\n    data: B\n  - version: "2025.4"\n    data: B\n''')
    result = DiffCompiler().compile(a, b)
    assert result.verified
    assert any(op['op'] == 'copy_item' for op in result.config['operations'])


def test_diff_compiler_complex_fallback_is_always_verified():
    a = parse('''db:\n  - version: "2025.6"\n    data: B\n  - name: "2025.3"\n    data: D\n''')
    b = parse('''db:\n  - version: "2026.4"\n    data: A\n  - name: "2025.6"\n    data: B\n''')
    result = DiffCompiler().compile(a, b)
    assert result.verified
    assert YamlPatchEngine().apply_document(deepcopy(a), result.config) == b


def test_discovery_fab_startswith_env(tmp_path):
    good = tmp_path/'FAB14-FZ1'/'STAGING'/'a'/'x.yaml'; good.parent.mkdir(parents=True); good.write_text('a: 1')
    bad = tmp_path/'FAB18-A'/'PROD'/'x.yaml'; bad.parent.mkdir(parents=True); bad.write_text('a: 1')
    found = discover(tmp_path, ['FAB14'], ['STAGING'])
    assert len(found) == 1 and found[0].fab == 'FAB14-FZ1' and found[0].env == 'STAGING'


def test_capture_original_then_transform_value():
    doc = parse('''db:\n  - version: "2025.6"\n    data: B\n''')
    cfg = {"version": 1, "variables": {"NEW": "2026.4"}, "operations": [
        {"op": "capture", "path": "$.db", "match": {"version": "2025.6"}, "as": "OLD", "expect_matches": 1},
        {"op": "replace", "path": "$.db", "value": [
            {"version": "{{ NEW }}", "data": "A"},
            {"name": "{{ OLD.version }}", "data": "{{ OLD.data }}"}
        ]}
    ]}
    out = YamlPatchEngine().apply_document(doc, cfg)
    assert out['db'][1] == {'name': '2025.6', 'data': 'B'}


def test_folder_compiler_complex_tree(tmp_path):
    from yaml_config_engine.folder_compiler import FolderCompiler
    before = tmp_path/'before'; after = tmp_path/'after'; generated = tmp_path/'generated'; output = tmp_path/'output'
    paths = [
        ('FAB14-FZ1/STAGING/deep/config.yaml', 'services:\n  - name: api\n    image: app:1\n', 'services:\n  - name: api\n    image: app:2\n  - name: worker\n    image: worker:1\n'),
        ('FAB18-A1/PROD/db.yaml', 'db:\n  - version: "2025.6"\n    data: B\n    nested:\n      x: 1\n', 'db:\n  - version: "2025.6"\n    data: B\n    nested:\n      x: 1\n  - version: "2025.4"\n    data: B\n    nested:\n      x: 1\n'),
    ]
    for rel, a, b in paths:
        ap = before/rel; bp = after/rel
        ap.parent.mkdir(parents=True, exist_ok=True); bp.parent.mkdir(parents=True, exist_ok=True)
        ap.write_text(a, encoding='utf-8'); bp.write_text(b, encoding='utf-8')
    result = FolderCompiler().compile_folder(before, after, generated, layout='expanded')
    assert result.verified
    FolderCompiler().apply_manifest(before, generated, output)
    assert FolderCompiler().verify_manifest(before, generated, after)


def test_yaml_output_indent_can_be_configured(tmp_path):
    src = tmp_path / "indent.yaml"
    src.write_text("root:\n  items:\n    - name: A\n", encoding="utf-8")
    cfg = {
        "version": 1,
        "options": {
            "yaml_output": {"mapping": 4, "sequence": 6, "offset": 4, "width": 120}
        },
        "operations": [
            {"op": "append", "path": "$.root.items", "value": {"name": "B", "enabled": True}}
        ],
    }
    YamlPatchEngine().apply_file(src, cfg)
    text = src.read_text(encoding="utf-8")
    assert "    items:" in text
    assert "    - name: B" in text or "    -   name: B" in text
    assert load_one(src)["root"]["items"][1]["name"] == "B"


def test_yaml_output_indent_defaults_remain_backward_compatible(tmp_path):
    src = tmp_path / "default-indent.yaml"
    src.write_text("root:\n  items:\n    - name: A\n", encoding="utf-8")
    cfg = {"version": 1, "operations": [{"op": "append", "path": "$.root.items", "value": {"name": "B"}}]}
    YamlPatchEngine().apply_file(src, cfg)
    text = src.read_text(encoding="utf-8")
    assert "  items:" in text
    assert "    - name: B" in text


def test_yaml_output_line_ending_crlf(tmp_path):
    src = tmp_path / "in.yaml"
    out = tmp_path / "out.yaml"
    src.write_bytes(b"root:\n  value: old\n")
    cfg = {
        "version": 1,
        "options": {"yaml_output": {"line_ending": "crlf"}},
        "operations": [{"op": "set", "path": "root.value", "value": "new"}],
    }
    from yaml_config_engine.engine import YamlPatchEngine
    YamlPatchEngine().apply_file(src, cfg, out)
    payload = out.read_bytes()
    assert b"\r\n" in payload
    assert b"\n" not in payload.replace(b"\r\n", b"")


def test_yaml_output_line_ending_preserves_crlf(tmp_path):
    src = tmp_path / "in.yaml"
    out = tmp_path / "out.yaml"
    src.write_bytes(b"root:\r\n  value: old\r\n")
    cfg = {
        "version": 1,
        "operations": [{"op": "set", "path": "root.value", "value": "new"}],
    }
    from yaml_config_engine.engine import YamlPatchEngine
    YamlPatchEngine().apply_file(src, cfg, out)
    assert b"\r\n" in out.read_bytes()

from copy import deepcopy
import pytest
from yaml_config_engine.engine import YamlPatchEngine
from yaml_config_engine.errors import ConfigError
from xml_config_engine.engine import XmlPatchEngine


def apply_yaml(doc, operations):
    return YamlPatchEngine().apply_document(deepcopy(doc), {"version": 1, "operations": operations})


def apply_xml(text, operations):
    return XmlPatchEngine().apply_text(text, {"version": 1, "format": "xml", "operations": operations})[0]


def test_yaml_mapping_star_updates_all_direct_children():
    doc = {"services": {"api": {"enabled": False}, "worker": {"enabled": False}}}
    out = apply_yaml(doc, [{"op": "set", "path": "$.services.*.enabled", "value": True}])
    assert out["services"]["api"]["enabled"] is True
    assert out["services"]["worker"]["enabled"] is True


def test_yaml_array_star_and_numeric_index_complex_combination():
    doc = {"apps": [
        {"name": "a", "routes": [{"path": "/a", "enabled": False}, {"path": "/health", "enabled": False}]},
        {"name": "b", "routes": [{"path": "/b", "enabled": False}, {"path": "/health", "enabled": False}]},
    ]}
    out = apply_yaml(doc, [
        {"op": "set", "path": "$.apps[*].routes[1].enabled", "value": True},
        {"op": "set", "path": "$.apps[0].routes[*].metadata", "missing": "create", "value": {"owner": "platform", "tags": ["managed", "v2"]}},
    ])
    assert [x["routes"][1]["enabled"] for x in out["apps"]] == [True, True]
    assert all(r["metadata"]["owner"] == "platform" for r in out["apps"][0]["routes"])
    assert "metadata" not in out["apps"][1]["routes"][0]


def test_yaml_nested_wildcards_update_large_sections():
    doc = {"regions": {
        "north": {"clusters": [{"name": "n1", "runtime": {"limits": {"cpu": 1}}}, {"name": "n2", "runtime": {"limits": {"cpu": 2}}}]},
        "south": {"clusters": [{"name": "s1", "runtime": {"limits": {"cpu": 3}}}]},
    }}
    value = {"cpu": 8, "memory": "16Gi", "policies": {"restart": "always", "health": ["ready", "live"]}}
    out = apply_yaml(doc, [{"op": "replace", "path": "$.regions.*.clusters[*].runtime.limits", "value": value}])
    assert out["regions"]["north"]["clusters"][1]["runtime"]["limits"] == value
    assert out["regions"]["south"]["clusters"][0]["runtime"]["limits"] == value


def test_yaml_array_value_match_with_nested_match_and_pattern():
    doc = {"versions": [
        {"name": "v1", "status": "legacy", "config": {"tier": "api", "enabled": True}},
        {"name": "v2", "status": "active", "config": {"tier": "api", "enabled": True}},
        {"name": "v3", "status": "active", "config": {"tier": "worker", "enabled": True}},
    ]}
    out = apply_yaml(doc, [{
        "op": "update_item", "path": "$.versions",
        "match": {"status": "active", "config.tier": {"$glob": "api*"}},
        "set": {"config.resources": {"requests": {"cpu": "500m"}, "limits": {"cpu": "2"}}, "config.enabled": False},
        "on_multiple_matches": "all",
    }])
    assert out["versions"][1]["config"]["enabled"] is False
    assert "resources" in out["versions"][1]["config"]
    assert out["versions"][2]["config"]["enabled"] is True


def test_yaml_wildcard_zero_match_skip_and_create_rejected():
    doc = {"services": {}}
    assert apply_yaml(doc, [{"op": "set", "path": "$.services.*.enabled", "value": True, "missing": "skip"}]) == doc
    with pytest.raises(ConfigError, match="not supported for wildcard paths"):
        apply_yaml(doc, [{"op": "set", "path": "$.services.*.enabled", "value": True, "missing": "create"}])


def test_xml_star_numeric_and_star_predicate_complex_combination():
    text = '''<configuration>\n  <apps>\n    <app name="a"><routes><route><enabled>false</enabled></route><route><enabled>false</enabled></route></routes></app>\n    <app name="b"><routes><route><enabled>false</enabled></route><route><enabled>false</enabled></route></routes></app>\n  </apps>\n</configuration>'''
    out = apply_xml(text, [
        {"op": "set", "path": "/configuration/apps/app[*]/routes/route[2]/enabled", "value": "true", "on_multiple_matches": "all"},
        {"op": "set", "path": "/configuration/apps/*[@name='a']/routes/route[*]/@managed", "value": "yes", "missing": "create", "on_multiple_matches": "all"},
    ])
    assert out.count('<enabled>true</enabled>') == 2
    assert out.count('managed="yes"') == 2


def test_xml_value_predicate_by_attribute_and_child_value():
    text = '''<root><versions>
      <version name="v1"><status>legacy</status><tier>api</tier><enabled>true</enabled></version>
      <version name="v2"><status>active</status><tier>api</tier><enabled>true</enabled></version>
      <version name="v3"><status>active</status><tier>worker</tier><enabled>true</enabled></version>
    </versions></root>'''
    out = apply_xml(text, [
        {"op": "set", "path": "/root/versions/version[@name='v2']/enabled", "value": "false"},
        {"op": "set", "path": "/root/versions/version[status='active']/@reviewed", "value": "yes", "missing": "create", "on_multiple_matches": "all"},
    ])
    assert '<version name="v2" reviewed="yes">' in out
    assert '<version name="v3" reviewed="yes">' in out
    assert '<enabled>false</enabled>' in out


def test_xml_numeric_index_is_xpath_one_based():
    text = '<root><item>A</item><item>B</item><item>C</item></root>'
    out = apply_xml(text, [{"op": "set", "path": "/root/item[2]", "value": "SECOND"}])
    assert out == '<root><item>A</item><item>SECOND</item><item>C</item></root>'

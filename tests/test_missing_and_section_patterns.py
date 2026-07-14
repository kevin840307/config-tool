from __future__ import annotations

import pytest

from yaml_config_engine.engine import YamlPatchEngine
from yaml_config_engine.errors import OperationError
from xml_config_engine.engine import XmlPatchEngine
from xml_config_engine.xmltext import XmlFormatError


def apply_yaml(document, *operations):
    return YamlPatchEngine().apply_document(document, {"operations": list(operations)})


def apply_xml(text, *operations):
    return XmlPatchEngine().apply_text(text, {"operations": list(operations)})[0]


def test_yaml_missing_error_is_safe_default_for_full_section():
    source = {"applications": {}}
    with pytest.raises(OperationError):
        apply_yaml(source, {
            "op": "set", "path": "$.applications.order-service", "value": {
                "replicas": 3, "resources": {"limits": {"cpu": "1", "memory": "1Gi"}}
            }
        })


def test_yaml_missing_skip_leaves_document_unchanged():
    source = {"applications": {"web": {"replicas": 2}}}
    result = apply_yaml(source, {
        "op": "replace", "path": "$.applications.optional-worker", "missing": "skip",
        "value": {"replicas": 1, "queues": ["low", "bulk"]}
    })
    assert result == source


def test_yaml_missing_create_adds_large_dict_and_list_section():
    source = {"applications": {"web": {"replicas": 2}}}
    result = apply_yaml(source, {
        "op": "set", "path": "$.applications.order-service", "missing": "create",
        "value": {
            "image": {"repository": "registry/order", "tag": "2.4.0"},
            "replicas": 3,
            "env": [
                {"name": "JAVA_OPTS", "value": "-Xms512m -Xmx1g"},
                {"name": "FEATURE_MODE", "value": "strict"},
            ],
            "resources": {
                "requests": {"cpu": "250m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            },
            "routes": [
                {"path": "/orders", "methods": ["GET", "POST"]},
                {"path": "/orders/{id}", "methods": ["GET", "PUT", "DELETE"]},
            ],
        }
    })
    assert result["applications"]["order-service"]["env"][1]["name"] == "FEATURE_MODE"
    assert result["applications"]["order-service"]["routes"][1]["methods"] == ["GET", "PUT", "DELETE"]


def test_yaml_exact_key_selector_replaces_entire_section():
    source = {"profiles": {"dev": {"db": {"host": "dev"}}, "prod": {"db": {"host": "old"}}}}
    result = apply_yaml(source, {
        "op": "replace", "path": "$.profiles", "key": "prod",
        "value": {
            "db": {"host": "prod-db", "pool": {"min": 5, "max": 30}},
            "cache": {"enabled": True, "ttlSeconds": 120},
            "features": ["audit", "metrics", "rate-limit"],
        }
    })
    assert result["profiles"]["dev"]["db"]["host"] == "dev"
    assert result["profiles"]["prod"]["db"]["pool"]["max"] == 30


def test_yaml_key_pattern_updates_multiple_large_sections():
    source = {
        "services": {
            "api-user": {"legacy": True},
            "api-order": {"legacy": True},
            "batch-cleanup": {"schedule": "0 0 * * *"},
        }
    }
    shared = {
        "deployment": {"replicas": 4, "strategy": {"type": "RollingUpdate", "maxUnavailable": 0}},
        "observability": {"metrics": True, "tracing": {"enabled": True, "sampleRate": 0.2}},
        "ports": [{"name": "http", "containerPort": 8080}, {"name": "management", "containerPort": 9090}],
    }
    result = apply_yaml(source, {
        "op": "set", "path": "$.services", "key_pattern": "api-*", "value": shared
    })
    assert result["services"]["api-user"] == shared
    assert result["services"]["api-order"] == shared
    assert result["services"]["batch-cleanup"]["schedule"] == "0 0 * * *"


def test_yaml_pattern_create_is_rejected_because_name_is_ambiguous():
    with pytest.raises(OperationError):
        apply_yaml({"services": {}}, {
            "op": "set", "path": "$.services", "key_pattern": "api-*", "missing": "create",
            "value": {"replicas": 2}
        })


def test_yaml_name_pattern_updates_nested_fields_in_list_sections():
    source = {
        "components": [
            {"name": "api-user", "config": {"timeout": 10, "retry": {"count": 1}}},
            {"name": "api-order", "config": {"timeout": 20, "retry": {"count": 2}}},
            {"name": "worker", "config": {"timeout": 60}},
        ]
    }
    result = apply_yaml(source, {
        "op": "update_item", "path": "$.components", "name_pattern": "api-*",
        "on_multiple_matches": "all",
        "set": {
            "config.timeout": 45,
            "config.retry": {"count": 5, "backoff": [1, 5, 15]},
            "config.circuitBreaker": {"enabled": True, "failureThreshold": 10},
        },
    })
    assert result["components"][0]["config"]["retry"]["backoff"] == [1, 5, 15]
    assert result["components"][1]["config"]["circuitBreaker"]["enabled"] is True
    assert result["components"][2]["config"]["timeout"] == 60


def test_yaml_name_pattern_missing_skip_for_optional_list_items():
    source = {"components": [{"name": "api", "config": {"enabled": True}}]}
    result = apply_yaml(source, {
        "op": "update_item", "path": "$.components", "name_pattern": "optional-*",
        "missing": "skip", "set": {"config.enabled": False}
    })
    assert result == source


def test_yaml_legacy_create_missing_and_new_missing_conflict_is_error():
    with pytest.raises(OperationError):
        apply_yaml({}, {
            "op": "set", "path": "$.new", "value": {"items": [1, 2]},
            "create_missing": True, "missing": "skip"
        })


XML_SOURCE = """<?xml version="1.0"?>
<configuration>
  <!-- keep -->
  <profiles>
    <profile-dev><database><host>dev-db</host></database></profile-dev>
    <profile-prod><database><host>old-db</host></database></profile-prod>
  </profiles>
  <components>
    <component><name>api-user</name><config><timeout>10</timeout></config></component>
    <component><name>api-order</name><config><timeout>20</timeout></config></component>
    <component><name>worker</name><config><timeout>60</timeout></config></component>
  </components>
</configuration>
"""


def test_xml_missing_error_is_safe_default():
    with pytest.raises(XmlFormatError):
        apply_xml(XML_SOURCE, {
            "op": "set", "path": "/configuration/not-found", "value": {"nested": {"enabled": True}}
        })


def test_xml_missing_skip_leaves_text_byte_identical():
    out = apply_xml(XML_SOURCE, {
        "op": "replace", "path": "/configuration/optional", "missing": "skip",
        "value": {"routes": {"route": [{"path": "/a"}, {"path": "/b"}]}}
    })
    assert out == XML_SOURCE


def test_xml_missing_create_adds_large_section():
    out = apply_xml(XML_SOURCE, {
        "op": "set", "path": "/configuration/orderService", "missing": "create",
        "value": {
            "image": {"repository": "registry/order", "tag": "2.4.0"},
            "resources": {"requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}},
            "routes": {"route": [{"path": "/orders", "method": "GET"}, {"path": "/orders", "method": "POST"}]},
        }
    })
    assert "<orderService>" in out
    assert "<repository>registry/order</repository>" in out
    assert out.count("<route>") == 2
    assert "<!-- keep -->" in out


def test_xml_exact_name_selector_replaces_entire_child_section():
    out = apply_xml(XML_SOURCE, {
        "op": "replace", "path": "/configuration/profiles", "name": "profile-prod",
        "value": {
            "database": {"host": "prod-db", "pool": {"min": "5", "max": "30"}},
            "cache": {"enabled": "true", "ttlSeconds": "120"},
        }
    })
    assert "<profile-dev><database><host>dev-db</host></database></profile-dev>" in out
    assert "<host>prod-db</host>" in out
    assert "<ttlSeconds>120</ttlSeconds>" in out


def test_xml_name_pattern_replaces_multiple_sections_only():
    out = apply_xml(XML_SOURCE, {
        "op": "set", "path": "/configuration/profiles", "name_pattern": "profile-*",
        "value": {
            "database": {"pool": {"min": "3", "max": "20"}},
            "features": {"feature": ["audit", "metrics"]},
        }
    })
    assert out.count("<max>20</max>") == 2
    assert out.count("<feature>audit</feature>") == 2
    assert "<components>" in out


def test_xml_name_pattern_create_is_rejected():
    with pytest.raises(XmlFormatError):
        apply_xml(XML_SOURCE, {
            "op": "set", "path": "/configuration/profiles", "name_pattern": "missing-*",
            "missing": "create", "value": {"enabled": "true"}
        })


def test_xml_update_item_name_pattern_updates_nested_section():
    out = apply_xml(XML_SOURCE, {
        "op": "update_item", "path": "/configuration/components", "element": "component",
        "name_pattern": "api-*", "on_multiple_matches": "all",
        "set": {
            "config.timeout": "45",
            "config.retry": {"count": "5", "backoff": {"seconds": ["1", "5", "15"]}},
            "config.circuitBreaker": {"enabled": "true", "failureThreshold": "10"},
        }
    })
    assert out.count("<timeout>45</timeout>") == 2
    assert out.count("<failureThreshold>10</failureThreshold>") == 2
    assert "<timeout>60</timeout>" in out


def test_xml_remove_name_pattern_skip_and_remove_multiple_sections():
    out = apply_xml(XML_SOURCE,
        {"op": "remove", "path": "/configuration/profiles", "name_pattern": "archive-*", "missing": "skip"},
        {"op": "remove", "path": "/configuration/profiles", "name_pattern": "profile-*", "on_multiple_matches": "all"},
    )
    assert "profile-dev" not in out and "profile-prod" not in out
    assert "<components>" in out

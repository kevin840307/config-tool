from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ruamel.yaml import YAML
from src.yaml_config_engine.comparison import strict_equal, strict_yaml_equal
from src.yaml_config_engine.diff_compiler import DiffCompiler
from src.yaml_config_engine.engine import YamlPatchEngine
from src.yaml_config_engine.yamlio import clone, dump_one, load_one


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_apply(before, after, *, name: str, max_ops: int | None = None):
    result = DiffCompiler(optimization_timeout_seconds=6, optimization_max_candidates=800).compile(before, after)
    require(result.verified, f"{name}: compile must verify")
    applied = YamlPatchEngine().apply_document(clone(before), result.config, track_no_effect=False)
    require(strict_yaml_equal(applied, after), f"{name}: apply result differs from after")
    if max_ops is not None:
        require(len(result.config.get("operations", [])) <= max_ops, f"{name}: too many operations: {len(result.config.get('operations', []))}")
    return result.config


def version(name: str, *, shadow: bool, cpu: str, memory: str, image: str, replicas: int):
    return {
        "name": name,
        "shadow": shadow,
        "replicas": replicas,
        "image": {"repository": "registry.local/enterprise/app", "tag": image, "pullPolicy": "IfNotPresent"},
        "resources": {
            "requests": {"cpu": cpu, "memory": memory},
            "limits": {"cpu": "2", "memory": "2Gi"},
        },
        "autoscaling": {
            "enabled": True,
            "minReplicas": 1,
            "maxReplicas": 10,
            "metrics": [
                {"type": "Resource", "resource": {"name": "cpu", "target": {"type": "Utilization", "averageUtilization": 70}}},
                {"type": "Resource", "resource": {"name": "memory", "target": {"type": "Utilization", "averageUtilization": 75}}},
            ],
        },
        "config": {
            "featureFlags": {"newRouting": False, "audit": True},
            "spring": {
                "profiles": ["base", "fab"],
                "datasource": {"url": "jdbc:postgresql://db/app", "pool": {"min": 2, "max": 20}},
            },
        },
    }


def scenario_app_phase_version():
    before = {
        "global": {"fab": "fab13", "env": "stg", "namespace": "enterprise-stg"},
        "apps": {
            "appA": {
                "enabled": True,
                "phases": {
                    "p1": {"versions": [version("v507", shadow=True, cpu="250m", memory="256Mi", image="2025.07", replicas=0), version("v509", shadow=True, cpu="250m", memory="256Mi", image="2025.09", replicas=1)]},
                    "p2": {"versions": [version("v507", shadow=True, cpu="250m", memory="256Mi", image="2025.07", replicas=0), version("v509", shadow=True, cpu="250m", memory="256Mi", image="2025.09", replicas=1)]},
                    "f13p1": {"versions": [version("v507", shadow=True, cpu="250m", memory="256Mi", image="2025.07", replicas=0), version("v509", shadow=True, cpu="250m", memory="256Mi", image="2025.09", replicas=1)]},
                },
            },
            "appB": {
                "enabled": True,
                "config": {"timeout": 30, "retry": [1, 5, 15]},
            },
        },
    }
    after = deepcopy(before)
    for phase in after["apps"]["appA"]["phases"].values():
        phase["versions"].pop(0)
        current = phase["versions"][0]
        current["shadow"] = False
        current["resources"]["requests"]["cpu"] = "500m"
        current["autoscaling"]["maxReplicas"] = 20
        current["config"]["featureFlags"]["newRouting"] = True
        new = deepcopy(current)
        new["name"] = "v510"
        new["shadow"] = True
        new["image"]["tag"] = "2025.10"
        phase["versions"].append(new)
    after["apps"]["appA"]["phases"]["f13p1"]["versions"][1]["resources"]["limits"]["cpu"] = "4"
    config = compile_apply(before, after, name="app-phase-version", max_ops=10)
    rendered = repr(config)
    require("phases/*/versions" in rendered or "paths" in rendered, "app-phase-version: common phase operations were not merged")


def scenario_k8s_workloads():
    before = {
        "workloads": {
            name: {
                "replicaCount": 2,
                "image": {"repository": f"registry.local/{name}", "tag": "1.0.0"},
                "containers": [
                    {"name": "main", "env": [{"name": "LOG_LEVEL", "value": "INFO"}, {"name": "TIMEOUT", "value": "30"}], "resources": {"requests": {"cpu": "250m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}}},
                    {"name": "sidecar", "env": [{"name": "MODE", "value": "watch"}], "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "100m", "memory": "128Mi"}}},
                ],
                "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [{"matchExpressions": [{"key": "node-pool", "operator": "In", "values": ["general", "compute"]}]}]}}},
                "topologySpreadConstraints": [{"maxSkew": 1, "topologyKey": "topology.kubernetes.io/zone", "whenUnsatisfiable": "ScheduleAnyway", "labelSelector": {"matchLabels": {"app": name}}}],
                "volumes": [{"name": "config", "configMap": {"name": f"{name}-config"}}, {"name": "secret", "secret": {"secretName": f"{name}-secret"}}],
            }
            for name in ("gateway", "orders", "scheduler")
        }
    }
    after = deepcopy(before)
    for name, workload in after["workloads"].items():
        workload["image"]["tag"] = "1.1.0"
        workload["replicaCount"] = 3
        workload["containers"][0]["resources"]["requests"]["cpu"] = "500m"
        workload["containers"][0]["env"][0]["value"] = "WARN"
        workload["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"].append("memory")
        workload["volumes"].append({"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}})
    after["workloads"]["scheduler"]["replicaCount"] = 1
    compile_apply(before, after, name="k8s-workloads", max_ops=9)


def scenario_mixed_types_and_order():
    before = {
        "settings": {
            "enabled": True,
            "nullValue": None,
            "integer": 1,
            "decimalText": "01.20",
            "booleanText": "false",
            "dateText": "2026-07-16",
            "emptyMap": {},
            "emptyList": [],
            "ports": [8080, 9090],
            "labels": {"a": "A", "b": "B", "c": "C"},
        }
    }
    after = {
        "settings": {
            "enabled": False,
            "integer": 2,
            "nullValue": None,
            "decimalText": "01.30",
            "booleanText": "true",
            "dateText": "2026-08-01",
            "emptyMap": {"created": True},
            "emptyList": ["first"],
            "ports": [8080, 9443, 9090],
            "labels": {"a": "A", "new": "N", "b": "B2", "c": "C"},
        }
    }
    compile_apply(before, after, name="mixed-types-order", max_ops=12)


def scenario_scalar_lists_and_duplicates():
    before = {
        "networkPolicy": {
            "ingressCidrs": ["10.0.0.0/24", "10.0.1.0/24", "10.0.1.0/24"],
            "ports": [80, 443, 8080],
            "protocols": ["TCP", "UDP"],
        },
        "featureOrder": ["auth", "audit", "routing", "metrics"],
    }
    after = {
        "networkPolicy": {
            "ingressCidrs": ["10.0.0.0/24", "10.0.2.0/24", "10.0.1.0/24"],
            "ports": [80, 8443, 443, 8080],
            "protocols": ["TCP"],
        },
        "featureOrder": ["auth", "routing", "audit", "metrics", "tracing"],
    }
    compile_apply(before, after, name="scalar-lists-duplicates", max_ops=12)


def scenario_quotes_comments_anchor():
    before_text = """# enterprise values\ndefaults: &defaults\n  enabled: true\n  mode: 'safe'\n  code: \"001\"\napps:\n  appA:\n    <<: *defaults\n    endpoint: 'https://old.example/api' # keep endpoint comment\n  appB:\n    <<: *defaults\n    endpoint: \"https://old.example/api\"\n"""
    after_text = """# enterprise values\ndefaults: &defaults\n  enabled: true\n  mode: 'strict'\n  code: \"002\"\napps:\n  appA:\n    <<: *defaults\n    endpoint: 'https://new.example/api' # keep endpoint comment\n  appB:\n    <<: *defaults\n    endpoint: \"https://new.example/api\"\n"""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    before = yaml.load(before_text)
    after = yaml.load(after_text)
    config = compile_apply(before, after, name="quotes-comments-anchor", max_ops=5)
    with tempfile.TemporaryDirectory() as td:
        before_path = Path(td) / "before.yaml"
        after_path = Path(td) / "after.yaml"
        result_path = Path(td) / "result.yaml"
        before_path.write_text(before_text, encoding="utf-8")
        after_path.write_text(after_text, encoding="utf-8")
        dump_one(YamlPatchEngine().apply_document(clone(load_one(before_path)), config, track_no_effect=False), result_path)
        result = load_one(result_path)
        expected = load_one(after_path)
        require(strict_equal(result, expected), "quotes-comments-anchor: file round-trip mismatch")
        text = result_path.read_text(encoding="utf-8")
        require("# keep endpoint comment" in text, "quotes-comments-anchor: inline comment lost")
        require("&defaults" in text and "*defaults" in text, "quotes-comments-anchor: anchor/alias lost")


def scenario_special_keys():
    before = {
        "annotations": {
            "nginx.ingress.kubernetes.io/proxy-body-size": "10m",
            "prometheus.io/scrape": "true",
            "a/b": "old",
            "a~b": "old2",
        },
        "map with spaces": {"key.with.dots": "x", "key[0]": "y"},
    }
    after = deepcopy(before)
    after["annotations"]["nginx.ingress.kubernetes.io/proxy-body-size"] = "50m"
    after["annotations"]["a/b"] = "new"
    after["annotations"]["a~b"] = "new2"
    after["map with spaces"]["key.with.dots"] = "z"
    compile_apply(before, after, name="special-keys", max_ops=8)


def scenario_shared_config_sections():
    before = {
        "apps": {
            app: {
                "config": {
                    "spring": {"profiles": {"active": ["base", "stg"]}, "jackson": {"timeZone": "Asia/Taipei"}},
                    "kafka": {"brokers": ["kafka-a:9092", "kafka-b:9092"], "consumer": {"groupId": app, "maxPollRecords": 500}},
                    "database": {"primary": {"url": f"jdbc:postgresql://db/{app}", "pool": {"min": 2, "max": 20}}, "readonly": {"enabled": False}},
                    "logging": {"level": {"root": "INFO", "com.company": "DEBUG"}, "json": True},
                }
            }
            for app in ("appA", "appB", "appC", "appD")
        }
    }
    after = deepcopy(before)
    for app in after["apps"].values():
        app["config"]["kafka"]["consumer"]["maxPollRecords"] = 1000
        app["config"]["database"]["primary"]["pool"]["max"] = 40
        app["config"]["database"]["readonly"]["enabled"] = True
        app["config"]["logging"]["level"]["root"] = "WARN"
        app["config"]["spring"]["profiles"]["active"].append("observability")
    after["apps"]["appD"]["config"]["logging"]["level"]["com.company"] = "TRACE"
    config = compile_apply(before, after, name="shared-config-sections", max_ops=7)
    require("$/apps/*/config" in repr(config) or "$/apps/*" in repr(config), "shared-config-sections: common app config not generalized")


def main() -> None:
    scenarios = [
        scenario_app_phase_version,
        scenario_k8s_workloads,
        scenario_mixed_types_and_order,
        scenario_scalar_lists_and_duplicates,
        scenario_quotes_comments_anchor,
        scenario_special_keys,
        scenario_shared_config_sections,
    ]
    for scenario in scenarios:
        scenario()
        print(f"PASS: {scenario.__name__}")
    print(f"PASS: enterprise YAML scenario matrix ({len(scenarios)} scenarios)")


if __name__ == "__main__":
    main()

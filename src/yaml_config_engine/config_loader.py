from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .yamlio import load_one
from .variable_scope import resolve_scope_variables


def _as_paths(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return list(value)
    raise ValueError('variable_map_file must be a path string or a list of path strings')


def _normalize_map_document(raw: Any, source: Path) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ValueError(f'{source}: variable map file must contain a YAML mapping')
    value = raw.get('variable_map', raw)
    if isinstance(value, dict):
        result: dict[str, dict[str, Any]] = {}
        for scope, variables in value.items():
            if not isinstance(variables, dict):
                raise ValueError(f'{source}: scope {scope!r} must map to a variables mapping')
            result[str(scope)] = dict(variables)
        return result
    if isinstance(value, list):
        result = {}
        for index, row in enumerate(value):
            if not isinstance(row, dict) or 'scope' not in row:
                raise ValueError(f'{source}: variable_map[{index}] requires scope and variables/values')
            variables = row.get('variables', row.get('values', {}))
            if not isinstance(variables, dict):
                raise ValueError(f'{source}: variable_map[{index}] variables must be a mapping')
            result[str(row['scope'])] = dict(variables)
        return result
    raise ValueError(f'{source}: variable_map must be a mapping or list')


def _normalize_inline(value: Any) -> dict[str, dict[str, Any]]:
    if value in (None, {}):
        return {}
    return _normalize_map_document({'variable_map': value}, Path('<inline>'))


def _merge_maps(base: dict[str, dict[str, Any]], overlay: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = deepcopy(base)
    for scope, variables in overlay.items():
        result.setdefault(scope, {}).update(deepcopy(variables))
    return result


def load_config_with_variable_maps(path: str | Path, override_variable_map_files: list[str | Path] | None = None) -> dict[str, Any]:
    """Load a config and resolve variable_map_file relative to that config.

    Precedence, from lowest to highest:
      earlier external files -> later external files -> inline variable_map.
    The same behavior is supported at rule level.
    """
    config_path = Path(path).resolve()
    raw = load_one(config_path)
    if not isinstance(raw, dict):
        raise ValueError(f'{config_path}: config root must be a mapping')
    result = deepcopy(raw)

    external: dict[str, dict[str, Any]] = {}
    for ref in _as_paths(result.get('variable_map_file')):
        ref_path = (config_path.parent / ref).resolve()
        external = _merge_maps(external, _normalize_map_document(load_one(ref_path), ref_path))
    result['variable_map'] = _merge_maps(external, _normalize_inline(result.get('variable_map')))

    # Runtime-supplied mapping files have higher priority than mappings declared
    # inside the config. Relative paths are resolved from the caller's CWD.
    for ref in override_variable_map_files or []:
        ref_path = Path(ref).expanduser().resolve()
        result['variable_map'] = _merge_maps(
            result['variable_map'], _normalize_map_document(load_one(ref_path), ref_path)
        )

    # Global values are always available. A standalone generated patch may
    # additionally declare the exact FAB/ENV scope used during compilation so
    # it can resolve the same external mapping without relying on folder paths.
    scope = result.get('scope') or result.get('variable_scope') or {}
    if scope is None:
        scope = {}
    if not isinstance(scope, dict):
        raise ValueError('scope must be a mapping with optional fab/env')
    fab = str(scope.get('fab', '') or '')
    env = str(scope.get('env', '') or '')
    scoped_values, matched_scopes = resolve_scope_variables(result['variable_map'], fab, env)
    result['variables'] = {**dict(result.get('variables') or {}), **deepcopy(scoped_values)}
    if matched_scopes:
        result['resolved_variable_scopes'] = matched_scopes

    rules = result.get('rules') or []
    if not isinstance(rules, list):
        raise ValueError('rules must be a list')
    normalized_rules = []
    for index, raw_rule in enumerate(rules):
        if not isinstance(raw_rule, dict):
            raise ValueError(f'rules[{index}] must be a mapping')
        rule = deepcopy(raw_rule)
        rule_external: dict[str, dict[str, Any]] = {}
        for ref in _as_paths(rule.get('variable_map_file')):
            ref_path = (config_path.parent / ref).resolve()
            rule_external = _merge_maps(rule_external, _normalize_map_document(load_one(ref_path), ref_path))
        rule['variable_map'] = _merge_maps(rule_external, _normalize_inline(rule.get('variable_map')))
        normalized_rules.append(rule)
    result['rules'] = normalized_rules
    return result

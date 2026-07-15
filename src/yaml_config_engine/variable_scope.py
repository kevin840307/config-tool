from __future__ import annotations
from typing import Any


def _normalize_table(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k): dict(v or {}) for k, v in raw.items()}
    if isinstance(raw, list):
        result: dict[str, dict[str, Any]] = {}
        for entry in raw:
            if not isinstance(entry, dict) or 'scope' not in entry:
                raise ValueError('variable_map list entries require scope and variables')
            result[str(entry['scope'])] = dict(entry.get('variables') or entry.get('values') or {})
        return result
    raise ValueError('variable_map must be a mapping or list')


def resolve_scope_variables(raw: Any, fab: str, env: str) -> tuple[dict[str, Any], list[str]]:
    """Resolve prefix-based FAB/ENV variables from least to most specific.

    Scope examples:
      FAB14
      FAB14-FZ1
      FAB14-FZ1:STAGING
    Both FAB and ENV portions use starts-with matching.
    """
    table = _normalize_table(raw)
    matched: list[tuple[tuple[int, int, int], str, dict[str, Any]]] = []
    for scope, values in table.items():
        if scope.lower() == 'global':
            matched.append(((-1, 0, 0), scope, values))
            continue
        fab_prefix, sep, env_prefix = scope.partition(':')
        if fab_prefix and not fab.startswith(fab_prefix):
            continue
        if sep and env_prefix and not env.startswith(env_prefix):
            continue
        # Generic scopes first; precise scopes later overwrite them.
        score = (1 if sep else 0, len(fab_prefix), len(env_prefix))
        matched.append((score, scope, values))
    matched.sort(key=lambda x: x[0])
    resolved: dict[str, Any] = {}
    scopes: list[str] = []
    for _, scope, values in matched:
        resolved.update(values)
        scopes.append(scope)
    return resolved, scopes

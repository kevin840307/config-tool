from __future__ import annotations

from copy import deepcopy
from typing import Any

from .engine import YamlPatchEngine
from .comparison import strict_equal

_VALUE_FIELDS = {"value", "values", "set", "merge", "replacement"}


def _scalar_key(value: Any) -> tuple[type, Any] | None:
    if isinstance(value, (str, int, float, bool)) or value is None:
        try:
            hash(value)
        except TypeError:
            return None
        return (type(value), value)
    return None


def _unique_scalar_variables(variables: dict[str, Any]) -> dict[tuple[type, Any], str]:
    buckets: dict[tuple[type, Any], list[str]] = {}
    for name, value in variables.items():
        key = _scalar_key(value)
        if key is not None:
            buckets.setdefault(key, []).append(str(name))
    return {key: names[0] for key, names in buckets.items() if len(names) == 1}


def _string_tokens(variables: dict[str, Any]) -> list[tuple[str, str]]:
    by_value: dict[str, list[str]] = {}
    for name, value in variables.items():
        if isinstance(value, str) and value:
            by_value.setdefault(value, []).append(str(name))
    unique = [(value, names[0]) for value, names in by_value.items() if len(names) == 1]
    # Prefer longer values; equal lengths stay deterministic by variable name.
    unique.sort(key=lambda item: (-len(item[0]), item[1]))
    return unique


def _generalize_string(value: str, tokens: list[tuple[str, str]]) -> str:
    if not value or not tokens:
        return value
    result: list[str] = []
    index = 0
    used = False
    while index < len(value):
        matches = [(text, name) for text, name in tokens if value.startswith(text, index)]
        if not matches:
            result.append(value[index])
            index += 1
            continue
        best_len = len(matches[0][0])
        best = [(text, name) for text, name in matches if len(text) == best_len]
        # Ambiguous same-length matches are intentionally left literal.
        if len(best) != 1:
            result.append(value[index])
            index += 1
            continue
        text, name = best[0]
        result.append("{{ " + name + " }}")
        index += len(text)
        used = True
    candidate = "".join(result)
    return candidate if used else value


def _generalize_payload(value: Any, exact: dict[tuple[type, Any], str], tokens: list[tuple[str, str]]) -> Any:
    key = _scalar_key(value)
    if key is not None and key in exact:
        template = "{{ " + exact[key] + " }}"
        return template
    if isinstance(value, str):
        generalized = _generalize_string(str(value), tokens)
        return generalized
    if isinstance(value, list):
        return [_generalize_payload(item, exact, tokens) for item in value]
    if isinstance(value, dict):
        return {k: _generalize_payload(v, exact, tokens) for k, v in value.items()}
    return deepcopy(value)



def _quote_style(value: Any) -> str:
    name = type(value).__name__
    if name == 'SingleQuotedScalarString': return 'single'
    if name == 'DoubleQuotedScalarString': return 'double'
    return 'plain'


def _collect_generalized_styles(original: Any, generalized: Any, prefix: str = '') -> dict[str, str]:
    styles: dict[str, str] = {}
    if isinstance(original, str) and isinstance(generalized, str) and '{{' in generalized:
        styles[prefix] = _quote_style(original)
        return styles
    if isinstance(original, dict) and isinstance(generalized, dict):
        for key in original.keys() & generalized.keys():
            child = f'{prefix}.{key}' if prefix else str(key)
            styles.update(_collect_generalized_styles(original[key], generalized[key], child))
    elif isinstance(original, list) and isinstance(generalized, list):
        for i, (a, b) in enumerate(zip(original, generalized)):
            child = f'{prefix}.{i}' if prefix else str(i)
            styles.update(_collect_generalized_styles(a, b, child))
    return styles

def generalize_operations(operations: list[dict[str, Any]], variables: dict[str, Any]) -> list[dict[str, Any]]:
    exact = _unique_scalar_variables(variables)
    tokens = _string_tokens(variables)
    output: list[dict[str, Any]] = []
    for raw in operations:
        op = deepcopy(raw)
        style_map: dict[str, str] = {}
        for field in _VALUE_FIELDS:
            if field in op:
                original = deepcopy(op[field])
                generalized = _generalize_payload(op[field], exact, tokens)
                op[field] = generalized
                styles = _collect_generalized_styles(original, generalized)
                if styles:
                    if '' in styles and len(styles) == 1 and field in {'value', 'replacement'}:
                        op['quote'] = styles['']
                    else:
                        for path, style in styles.items():
                            style_map[f'{field}.{path}' if path else field] = style
        if style_map:
            op['quote_styles'] = style_map
        nested = op.get("item_operations")
        if isinstance(nested, list):
            op["item_operations"] = generalize_operations(nested, variables)
        output.append(op)
    return output


def _without_redundant_plain_quotes(config: dict[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(config)
    def clean(ops: list[dict[str, Any]]) -> None:
        for op in ops:
            if op.get('quote') == 'plain':
                op.pop('quote', None)
            styles = op.get('quote_styles')
            if isinstance(styles, dict):
                kept = {k: v for k, v in styles.items() if v != 'plain'}
                if kept: op['quote_styles'] = kept
                else: op.pop('quote_styles', None)
            nested = op.get('item_operations')
            if isinstance(nested, list): clean(nested)
    clean(candidate.get('operations', []))
    return candidate


def _replays(before: Any, after: Any, candidate: dict[str, Any], variables: dict[str, Any]) -> bool:
    try:
        actual = YamlPatchEngine().apply_document(deepcopy(before), candidate, variables)
    except Exception:
        return False
    return strict_equal(actual, after)


def verified_generalize_config(before: Any, after: Any, config: dict[str, Any], variables: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    explicit = deepcopy(config)
    explicit["operations"] = generalize_operations(explicit.get("operations", []), variables)
    if explicit.get("operations") == config.get("operations"):
        return explicit, True
    automatic = _without_redundant_plain_quotes(explicit)
    if _replays(before, after, automatic, variables):
        return automatic, True
    if _replays(before, after, explicit, variables):
        return explicit, True
    return deepcopy(config), False


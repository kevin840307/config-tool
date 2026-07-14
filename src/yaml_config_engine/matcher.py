from __future__ import annotations
import re
import fnmatch
from typing import Any
from .pathing import get_node

_MISSING = object()


def _compare(actual: Any, condition: Any) -> bool:
    if isinstance(condition, dict) and any(str(k).startswith('$') for k in condition):
        for op, expected in condition.items():
            if op == '$eq' and actual != expected: return False
            if op == '$ne' and actual == expected: return False
            if op == '$in' and actual not in expected: return False
            if op == '$not_in' and actual in expected: return False
            if op == '$contains':
                try:
                    if expected not in actual: return False
                except TypeError: return False
            if op == '$starts_with' and (not isinstance(actual, str) or not actual.startswith(str(expected))): return False
            if op == '$ends_with' and (not isinstance(actual, str) or not actual.endswith(str(expected))): return False
            if op == '$glob' and (not isinstance(actual, str) or fnmatch.fnmatchcase(actual, str(expected)) is False): return False
            if op == '$iglob' and (not isinstance(actual, str) or fnmatch.fnmatch(actual.lower(), str(expected).lower()) is False): return False
            if op == '$regex' and (not isinstance(actual, str) or re.search(str(expected), actual) is None): return False
            if op == '$iregex' and (not isinstance(actual, str) or re.search(str(expected), actual, re.I) is None): return False
            if op == '$gt' and not (actual > expected): return False
            if op == '$gte' and not (actual >= expected): return False
            if op == '$lt' and not (actual < expected): return False
            if op == '$lte' and not (actual <= expected): return False
            if op == '$between' and not (expected[0] <= actual <= expected[1]): return False
            if op == '$type':
                names = {'str': str, 'int': int, 'float': float, 'bool': bool, 'list': list, 'dict': dict, 'null': type(None)}
                if not isinstance(actual, names.get(str(expected), object)): return False
            if op == '$exists' and (actual is not _MISSING) != bool(expected): return False
        return True
    return actual == condition


def matches(item: Any, spec: dict[str, Any]) -> bool:
    for key, expected in spec.items():
        if key in ('all', '$all'):
            if not all(matches(item, x) for x in expected): return False
        elif key in ('any', '$any'):
            if not any(matches(item, x) for x in expected): return False
        elif key in ('not', '$not'):
            if matches(item, expected): return False
        else:
            try:
                actual = get_node(item, key) if '.' in key or '[' in key or str(key).startswith('$') else item[key]
            except Exception:
                actual = _MISSING
            if not _compare(actual, expected): return False
    return True


def find_indices(seq: list[Any], spec: dict[str, Any]) -> list[int]:
    return [i for i, item in enumerate(seq) if isinstance(item, (dict, list)) and matches(item, spec)]

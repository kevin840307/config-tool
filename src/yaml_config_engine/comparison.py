from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class ComparisonResult:
    equal: bool
    differences: list[dict[str, Any]]


def _type_name(value: Any) -> str:
    return type(value).__name__


def _string_style(value: Any) -> str:
    """Return the YAML scalar presentation style relevant to round-trip output."""
    name = type(value).__name__
    if name == 'SingleQuotedScalarString':
        return 'single'
    if name == 'DoubleQuotedScalarString':
        return 'double'
    if name in {'LiteralScalarString', 'FoldedScalarString'}:
        return name
    return 'plain'


def strict_compare(actual: Any, expected: Any, path: str = '$', *, max_differences: int = 100) -> ComparisonResult:
    """Compare YAML data including scalar type, mapping key order, list order and values."""
    differences: list[dict[str, Any]] = []

    def add(kind: str, p: str, a: Any, e: Any) -> None:
        if len(differences) < max_differences:
            differences.append({'path': p, 'kind': kind, 'actual': a, 'expected': e})

    def walk(a: Any, e: Any, p: str) -> None:
        if len(differences) >= max_differences:
            return
        # Container implementations (dict vs CommentedMap, list vs CommentedSeq)
        # are equivalent; scalar YAML types remain strict (bool != int, str != number).
        if isinstance(a, dict) and isinstance(e, dict):
            ak, ek = list(a.keys()), list(e.keys())
            if ak != ek:
                add('mapping_order_or_keys', p, ak, ek)
            # Compare values by expected key so ordering differences do not hide value differences.
            for key in ek:
                child = f"{p}/{str(key).replace('~','~0').replace('/','~1')}"
                if key not in a:
                    add('missing_key', child, None, e[key])
                else:
                    walk(a[key], e[key], child)
            for key in ak:
                if key not in e:
                    child = f"{p}/{str(key).replace('~','~0').replace('/','~1')}"
                    add('unexpected_key', child, a[key], None)
            return
        if isinstance(a, list) and isinstance(e, list):
            if len(a) != len(e):
                add('list_length', p, len(a), len(e))
            for i, (av, ev) in enumerate(zip(a, e)):
                walk(av, ev, f'{p}/{i}')
            return
        if isinstance(a, str) and isinstance(e, str):
            actual_style, expected_style = _string_style(a), _string_style(e)
            if actual_style != expected_style:
                add('quote_style', p, actual_style, expected_style)
                return
        elif type(a) is not type(e):
            add('type', p, _type_name(a), _type_name(e)); return
        if a != e:
            add('value', p, a, e)

    walk(actual, expected, path)
    return ComparisonResult(not differences, differences)


def strict_equal(actual: Any, expected: Any) -> bool:
    return strict_compare(actual, expected, max_differences=1).equal


def strict_documents_equal(actual: list[Any], expected: list[Any]) -> bool:
    if len(actual) != len(expected):
        return False
    return all(strict_equal(a, e) for a, e in zip(actual, expected))


def _contains_merge_metadata(value: Any, seen: set[int] | None = None) -> bool:
    seen = seen or set()
    identity = id(value)
    if identity in seen:
        return False
    seen.add(identity)
    if getattr(value, 'merge', None):
        return True
    if isinstance(value, dict):
        return any(_contains_merge_metadata(k, seen) or _contains_merge_metadata(v, seen) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_merge_metadata(v, seen) for v in value)
    return False


def strict_yaml_equal(actual: Any, expected: Any) -> bool:
    """Strict YAML equality with correct merge-key semantics.

    ruamel caches values inherited through ``<<``. Updating an anchor source does
    not refresh those cached lookups until the YAML is serialized and parsed again.
    Compiler replay must therefore normalize merge-key documents before comparing,
    otherwise a correct anchor-only update is rejected and aliases are materialized.
    """
    if not (_contains_merge_metadata(actual) or _contains_merge_metadata(expected)):
        return strict_equal(actual, expected)
    from io import StringIO
    from ruamel.yaml import YAML
    yaml = YAML(typ='rt')
    yaml.preserve_quotes = True
    def normalize(value: Any) -> Any:
        out = StringIO()
        yaml.dump(value, out)
        return yaml.load(out.getvalue())
    return strict_equal(normalize(actual), normalize(expected))

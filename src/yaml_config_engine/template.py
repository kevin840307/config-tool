from __future__ import annotations
from copy import deepcopy
from typing import Any
from functools import lru_cache
from jinja2 import Environment, StrictUndefined, Undefined
from ruamel.yaml.comments import CommentedMap, CommentedSeq

_env = Environment(undefined=StrictUndefined, autoescape=False)


@lru_cache(maxsize=4096)
def _compiled_expression(expr: str):
    return _env.compile_expression(expr, undefined_to_none=False)


@lru_cache(maxsize=4096)
def _compiled_template(value: str):
    return _env.from_string(value)


def _render_string(value: str, context: dict[str, Any]) -> Any:
    # Most configuration scalar strings are literals. Avoid invoking Jinja at
    # all unless the value actually contains template syntax. This is semantic-
    # preserving and removes the dominant replay cost for large YAML files.
    if "{{" not in value and "{%" not in value and "{#" not in value:
        return value
    stripped = value.strip()
    if stripped.startswith("{{") and stripped.endswith("}}") and stripped.count("{{") == 1:
        expr = stripped[2:-2].strip()
        rendered = _compiled_expression(expr)(**context)
        if isinstance(rendered, Undefined):
            # ``compile_expression(..., undefined_to_none=False)`` returns the
            # StrictUndefined object without forcing evaluation. Convert it to
            # text only to trigger the intended UndefinedError immediately.
            str(rendered)
    else:
        rendered = _compiled_template(value).render(**context)
    # Template quoting is YAML syntax only. Quote output style is controlled by
    # the operation quote/quote_styles metadata or by target-node preservation.
    return str(rendered) if isinstance(rendered, str) else rendered



def render_value(value: Any, context: dict[str, Any]) -> Any:
    """Render templates without flattening ruamel round-trip containers.

    Rebuilding CommentedMap/CommentedSeq as plain dict/list discards comments,
    anchors, quote style, and formatting metadata. Existing round-trip
    containers are therefore mutated on a deepcopy. Plain config containers
    are promoted to fresh round-trip containers so newly inserted structured
    sections can safely receive relocated boundary comments without inheriting
    the quote style of template literals such as ``"{{ VALUE }}"``.
    """
    if isinstance(value, str):
        return _render_string(value, context)
    if isinstance(value, list):
        if not isinstance(value, CommentedSeq):
            return CommentedSeq(render_value(item, context) for item in value)
        result = deepcopy(value)
        for index, item in enumerate(value):
            result[index] = render_value(item, context)
        return result
    if isinstance(value, dict):
        if not isinstance(value, CommentedMap):
            result = CommentedMap()
            for key, item in value.items():
                result[render_value(key, context)] = render_value(item, context)
            return result
        result = deepcopy(value)
        original_keys = list(value.keys())
        for index, key in enumerate(original_keys):
            rendered_key = render_value(key, context)
            rendered_value = render_value(value[key], context)
            if rendered_key == key:
                # Replacing a quoted template scalar in-place makes ruamel keep
                # the template literal's quote style. Remove/reinsert instead so
                # quote style is driven by quote metadata or the destination node.
                original_value = value[key]
                if isinstance(original_value, str) and "{{" in str(original_value) and isinstance(rendered_value, str):
                    comment = None
                    ca = getattr(result, 'ca', None)
                    if ca is not None:
                        comment = getattr(ca, 'items', {}).pop(key, None)
                    result.pop(key, None)
                    result.insert(min(index, len(result)), key, rendered_value)
                    if comment is not None and ca is not None:
                        ca.items[key] = comment
                else:
                    result[key] = rendered_value
                continue
            # Preserve order and mapping-level comment metadata on templated keys.
            comment = None
            ca = getattr(result, 'ca', None)
            if ca is not None:
                comment = getattr(ca, 'items', {}).pop(key, None)
            result.pop(key, None)
            result.insert(min(index, len(result)), rendered_key, rendered_value)
            if comment is not None and ca is not None:
                ca.items[rendered_key] = comment
        return result
    return deepcopy(value)

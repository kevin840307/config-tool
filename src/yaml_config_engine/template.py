from __future__ import annotations
from typing import Any
from jinja2 import Environment, StrictUndefined

_env = Environment(undefined=StrictUndefined, autoescape=False)


def render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{{") and stripped.endswith("}}") and stripped.count("{{") == 1:
            expr = stripped[2:-2].strip()
            return _env.compile_expression(expr, undefined_to_none=False)(**context)
        return _env.from_string(value).render(**context)
    if isinstance(value, list):
        return [render_value(v, context) for v in value]
    if isinstance(value, dict):
        return {render_value(k, context): render_value(v, context) for k, v in value.items()}
    return value

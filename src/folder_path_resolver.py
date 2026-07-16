from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import glob

from yaml_config_engine.template import render_value
from yaml_config_engine.variable_scope import resolve_scope_variables

_GLOB_CHARS = set('*?[')


def _safe_relative(text: str) -> str:
    normalized = str(text).replace('\\', '/').strip()
    if not normalized:
        raise ValueError('Folder patch file path cannot be empty')
    path = PurePosixPath(normalized)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe folder patch file path: {text!r}')
    if any(part in {'', '.'} for part in path.parts):
        raise ValueError(f'Invalid folder patch file path: {text!r}')
    return path.as_posix()


def base_path_variables(patch: dict[str, Any], runtime_variables: dict[str, Any] | None) -> dict[str, Any]:
    """Variables available before a concrete file path/FAB/ENV is known.

    File-key templates intentionally use patch variables, the global variable-map
    scope, and runtime variables. FAB/ENV-specific scopes are resolved after a
    concrete relative path has been selected.
    """
    global_vars, _ = resolve_scope_variables(patch.get('variable_map', {}), '', '')
    merged = dict(patch.get('variables') or {})
    merged.update(global_vars)
    merged.update(runtime_variables or {})
    return merged


def resolve_file_keys(
    output_root: Path,
    raw_key: str,
    patch: dict[str, Any],
    runtime_variables: dict[str, Any] | None = None,
) -> list[str]:
    rendered = render_value(str(raw_key), base_path_variables(patch, runtime_variables))
    if not isinstance(rendered, str):
        rendered = str(rendered)
    pattern = _safe_relative(rendered)
    if not any(ch in pattern for ch in _GLOB_CHARS):
        return [pattern]

    # glob() receives an already safety-checked relative pattern. Only files
    # beneath output_root are returned; directories are never patch targets.
    absolute_pattern = str(output_root / Path(pattern))
    matches: list[str] = []
    for value in glob.glob(absolute_pattern, recursive=True):
        candidate = Path(value)
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            rel = resolved.relative_to(output_root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f'Wildcard escaped output root: {raw_key!r}') from exc
        matches.append(rel)
    return sorted(set(matches))

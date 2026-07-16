from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any
import glob
import json


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))


def _existing_matches(source_root: Path, pattern: str) -> set[str]:
    matches: set[str] = set()
    for raw in glob.glob(str(source_root / Path(pattern)), recursive=True):
        path = Path(raw)
        if path.is_file():
            matches.add(path.resolve().relative_to(source_root.resolve()).as_posix())
    return matches


def _template_path(path: str, variables: dict[str, Any]) -> str:
    parts = list(PurePosixPath(path).parts)
    candidates = sorted(
        ((name, str(value)) for name, value in variables.items() if str(value)),
        key=lambda item: len(item[1]), reverse=True,
    )
    for index, part in enumerate(parts):
        rendered = part
        # Replace variables inside a path segment as well as whole segments,
        # e.g. app-v512-config -> app-{{ config_version }}-config.
        for name, value in candidates:
            if value in rendered:
                rendered = rendered.replace(value, '{{ ' + str(name) + ' }}')
        parts[index] = rendered
    return PurePosixPath(*parts).as_posix()


def _star_candidate(paths: list[str]) -> str | None:
    split = [PurePosixPath(path).parts for path in paths]
    if len(paths) < 2 or len({len(parts) for parts in split}) != 1:
        return None
    width = len(split[0])
    differing = [index for index in range(width) if len({parts[index] for parts in split}) > 1]
    if not differing:
        return None
    candidate = list(split[0])
    for index in differing:
        candidate[index] = '*'
    return PurePosixPath(*candidate).as_posix()


def generalize_file_map(
    files: dict[str, Any],
    source_root: str | Path,
    *,
    variables: dict[str, Any] | None = None,
    enable_wildcards: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Safely generalize concrete compact-patch file keys.

    Variable templates are applied only when explicitly supplied. Wildcards are
    emitted only when the pattern's existing-file match set is exactly the same
    as the concrete source paths being merged. Create-only targets are therefore
    never converted to wildcards.
    """
    source_root = Path(source_root).resolve()
    variables = dict(variables or {})
    working: dict[str, Any] = dict(files)
    report: dict[str, Any] = {'variables': [], 'wildcards': []}

    # Explicit variables are deterministic and may also cover create targets.
    if variables:
        templated: dict[str, Any] = {}
        valid = True
        changes: list[dict[str, str]] = []
        for raw_path, spec in working.items():
            candidate = _template_path(raw_path, variables)
            if candidate in templated:
                valid = False
                break
            templated[candidate] = spec
            if candidate != raw_path:
                changes.append({'from': raw_path, 'to': candidate})
        if valid:
            working = templated
            report['variables'] = changes

    if not enable_wildcards:
        return working, report

    # Wildcards operate on concrete keys only. Templated paths remain standalone.
    concrete = {key: value for key, value in working.items() if '{{' not in key}
    groups: dict[str, list[str]] = defaultdict(list)
    for path, spec in concrete.items():
        if (source_root / Path(path)).is_file():
            groups[_canonical(spec)].append(path)

    consumed: set[str] = set()
    replacements: dict[str, Any] = {}
    for paths in sorted(groups.values(), key=lambda values: (-len(values), values)):
        paths = sorted(path for path in paths if path not in consumed)
        if len(paths) < 2:
            continue
        expected = set(paths)
        candidates: list[str] = []
        star = _star_candidate(paths)
        if star:
            candidates.append(star)
        basenames = {PurePosixPath(path).name for path in paths}
        if len(basenames) == 1:
            candidates.append('**/' + next(iter(basenames)))
        chosen = next((candidate for candidate in candidates if _existing_matches(source_root, candidate) == expected), None)
        if chosen is None or chosen in working or chosen in replacements:
            continue
        replacements[chosen] = deepcopy(working[paths[0]])
        consumed.update(paths)
        report['wildcards'].append({'from': paths, 'to': chosen})

    if consumed:
        result = {key: value for key, value in working.items() if key not in consumed}
        result.update(replacements)
        working = dict(sorted(result.items()))
    return working, report


def load_compile_path_context(
    variable_map_files: list[str | Path] | None = None,
    variables: dict[str, Any] | None = None,
    fab: str = '',
    env: str = '',
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    from yaml_config_engine.config_loader import _merge_maps, _normalize_map_document
    from yaml_config_engine.variable_scope import resolve_scope_variables
    from yaml_config_engine.yamlio import load_one

    variable_map: dict[str, dict[str, Any]] = {}
    for ref in variable_map_files or []:
        path = Path(ref).expanduser().resolve()
        variable_map = _merge_maps(variable_map, _normalize_map_document(load_one(path), path))
    resolved, _ = resolve_scope_variables(variable_map, str(fab or ''), str(env or ''))
    merged = dict(resolved)
    merged.update(variables or {})
    return merged, variable_map

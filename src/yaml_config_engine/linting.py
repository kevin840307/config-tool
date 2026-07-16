from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

from .models import EngineConfig
from .yamlio import load_one
from .config_loader import load_config_with_variable_maps

_TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\b")
_BUILTIN_VARS = {
    'FAB', 'ENV', 'PATH', 'RELATIVE_PATH', 'APP_PATH', 'FILE_NAME', 'FILE_STEM'
}


@dataclass(frozen=True)
class LintIssue:
    severity: str
    code: str
    message: str
    location: str = '$'
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class LintReport:
    issues: list[LintIssue]

    @property
    def errors(self) -> list[LintIssue]:
        return [x for x in self.issues if x.severity == 'error']

    @property
    def warnings(self) -> list[LintIssue]:
        return [x for x in self.issues if x.severity == 'warning']

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            'valid': self.valid,
            'summary': {'errors': len(self.errors), 'warnings': len(self.warnings)},
            'issues': [x.to_dict() for x in self.issues],
        }


def _walk(value: Any, location: str = '$') -> Iterable[tuple[str, Any]]:
    yield location, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f'{location}/{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f'{location}[{index}]')


def _template_names(value: Any) -> set[str]:
    names: set[str] = set()
    for _, child in _walk(value):
        if isinstance(child, str):
            names.update(_TEMPLATE_RE.findall(child))
    return names


def _variable_map_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for mapping in value.values():
            if isinstance(mapping, dict):
                names.update(str(k) for k in mapping)
    elif isinstance(value, list):
        for row in value:
            if isinstance(row, dict) and isinstance(row.get('values'), dict):
                names.update(str(k) for k in row['values'])
    return names


class ConfigLinter:
    """Static, deterministic lint checks for human-authored enterprise configs."""

    def lint(self, config: str | Path | dict[str, Any], *, source_root: str | Path | None = None,
             plan: dict[str, Any] | None = None, extra_variables: dict[str, Any] | set[str] | None = None) -> LintReport:
        raw = load_config_with_variable_maps(config) if isinstance(config, (str, Path)) else config
        cfg = EngineConfig.model_validate(raw)
        issues: list[LintIssue] = []

        ids: dict[str, int] = {}
        for i, rule in enumerate(cfg.rules):
            rid = str(rule['id'])
            if rid in ids:
                issues.append(LintIssue('error', 'DUPLICATE_RULE_ID',
                    f'Rule id {rid!r} is duplicated.', f'$.rules[{i}].id',
                    'Use a unique id for every rule.'))
            ids[rid] = i

        extra_names = set(extra_variables or {})
        declared_global = set(cfg.variables) | _variable_map_names(cfg.variable_map) | _BUILTIN_VARS | extra_names
        used_all: set[str] = set()

        groups: list[tuple[str, list[dict[str, Any]], set[str]]] = [
            ('$.operations', cfg.operations, declared_global),
        ]
        for i, rule in enumerate(cfg.rules):
            declared = declared_global | set(rule.get('variables', {})) | _variable_map_names(rule.get('variable_map', {}))
            groups.append((f'$.rules[{i}].operations', rule['operations'], declared))

        for base, operations, declared in groups:
            used = _template_names(operations)
            used_all |= used
            for name in sorted(used - declared):
                issues.append(LintIssue('error', 'UNDEFINED_VARIABLE',
                    f'Template variable {name!r} is not declared by variables, variable_map, rule variables, or built-in file context.',
                    base, 'Declare it or pass it with --var NAME=VALUE.'))
            for j, op in enumerate(operations):
                loc = f'{base}[{j}]'
                name = op.get('op')
                position = op.get('position', {})
                if isinstance(position, dict) and 'index' in position:
                    issues.append(LintIssue('warning', 'INDEX_POSITION',
                        'Index-based placement is fragile when ordering changes.', f'{loc}.position.index',
                        'Prefer before_key/after_key for mappings or before/after match for lists.'))
                if name in {'append', 'prepend', 'insert', 'insert_at', 'insert_before', 'insert_after'}:
                    if not op.get('duplicate') and not op.get('unique_by'):
                        issues.append(LintIssue('warning', 'POTENTIALLY_NON_IDEMPOTENT_INSERT',
                            f'{name} may add duplicates on repeated execution.', loc,
                            'Use upsert_item or configure duplicate.unique_by and policy.'))
                if name in {'copy_item', 'copy_item_to_node'} and not op.get('duplicate'):
                    issues.append(LintIssue('warning', 'COPY_WITHOUT_DUPLICATE_GUARD',
                        'copy_item may duplicate the copied item on repeated execution.', loc,
                        'Add duplicate.unique_by and duplicate.policy.'))
                if name in {'update_item', 'remove_item', 'move_item', 'copy_item', 'copy_item_to_node'}:
                    match = op.get('match') or (op.get('source') or {}).get('match')
                    expectation = op.get('expect_matches') or (op.get('source') or {}).get('expect_matches')
                    optional_unique_remove = (
                        name == 'remove_item'
                        and op.get('on_zero_matches') == 'ignore'
                        and op.get('on_multiple_matches', 'error') == 'error'
                    )
                    if match is not None and expectation is None and not optional_unique_remove:
                        issues.append(LintIssue('warning', 'MATCH_WITHOUT_EXPECTATION',
                            'Content match does not explicitly require a unique match.', loc,
                            'Set expect_matches: 1 to prevent accidental multi-match changes.'))

        declared_user = set(cfg.variables) | _variable_map_names(cfg.variable_map)
        for rule in cfg.rules:
            declared_user |= set(rule.get('variables', {})) | _variable_map_names(rule.get('variable_map', {}))
        for name in sorted(declared_user - used_all):
            issues.append(LintIssue('warning', 'UNUSED_VARIABLE',
                f'Variable {name!r} is declared but not referenced by any operation.', '$.variables',
                'Remove it if it is no longer needed.'))

        if cfg.rules and not any(rule.get('enabled', True) for rule in cfg.rules):
            issues.append(LintIssue('error', 'ALL_RULES_DISABLED', 'All rules are disabled.', '$.rules'))

        if plan is not None:
            if plan.get('conflicts'):
                severity = 'error' if plan.get('conflict_policy') == 'error' else 'warning'
                issues.append(LintIssue(severity, 'RULE_WRITE_CONFLICT',
                    f"Plan contains {len(plan['conflicts'])} write conflict(s).", '$.rules',
                    'Separate targets, adjust priority/stop, or explicitly choose a conflict policy.'))
            matched = plan.get('summary', {}).get('matched_files', 0)
            if matched == 0:
                issues.append(LintIssue('error', 'ZERO_MATCHED_FILES',
                    'No YAML files match this config.', '$.rules',
                    'Check source_root and FAB/ENV/path allow/deny filters.'))
            counts = plan.get('summary', {}).get('rules', {})
            for rid, count in counts.items():
                if count == 0:
                    issues.append(LintIssue('warning', 'RULE_MATCHES_ZERO_FILES',
                        f'Rule {rid!r} matches zero files.', f'$.rules[{ids.get(rid, "?")}].filters'))

        return LintReport(issues)

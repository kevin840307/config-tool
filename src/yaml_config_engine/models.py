from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_OPS = {
    'set','replace','remove','merge','rename_key','insert_key','copy_key','move_key',
    'append','prepend','insert','insert_at','insert_before','insert_after',
    'update_item','upsert_item','remove_item','move_item','copy_item','capture',
    'copy_node','move_node','copy_item_to_node'
}


def normalize_operation(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept concise, readable aliases and return one canonical operation shape."""
    op = dict(raw)
    if 'target' in op and 'path' not in op:
        op['path'] = op.pop('target')
    if 'change' in op and 'set' not in op:
        op['set'] = op.pop('change')
    if 'find' in op and 'match' not in op:
        op['match'] = op.pop('find')
    if 'from' in op and op.get('op') == 'copy_item' and 'source' not in op:
        source = op.pop('from')
        op['source'] = source if isinstance(source, dict) and ('match' in source or 'index' in source) else {'match': source}
    if 'before' in op and 'position' not in op:
        op['position'] = {'before': {'match': op.pop('before')}}
    if 'after' in op and 'position' not in op:
        op['position'] = {'after': {'match': op.pop('after')}}
    # Unified readable placement DSL shared by mapping/list/copy/move operations.
    # Examples: place: top | bottom | {after_key: size} | {before: {name: A}}
    if 'place' in op and 'position' not in op:
        place = op.pop('place')
        if place in ('top', 'first'):
            op['position'] = {'first': True}
        elif place in ('bottom', 'last'):
            op['position'] = {'last': True}
        elif isinstance(place, int):
            op['position'] = {'index': place}
        elif isinstance(place, dict):
            if 'before' in place and not isinstance(place['before'], dict):
                raise ValueError('place.before must be a match mapping')
            if 'after' in place and not isinstance(place['after'], dict):
                raise ValueError('place.after must be a match mapping')
            if 'before' in place:
                op['position'] = {'before': {'match': place['before'], 'expect_matches': place.get('expect_matches', 1)}}
            elif 'after' in place:
                op['position'] = {'after': {'match': place['after'], 'expect_matches': place.get('expect_matches', 1)}}
            else:
                op['position'] = dict(place)
        else:
            raise ValueError(f'Unsupported place value: {place}')
    if op.pop('after_source', False):
        source = op.get('source', {})
        if 'match' in source:
            op['position'] = {'after': {'match': source['match'], 'expect_matches': source.get('expect_matches', 1)}}
        elif 'index' in source:
            op['position'] = {'after': {'index': source['index']}}
    if op.pop('before_source', False):
        source = op.get('source', {})
        if 'match' in source:
            op['position'] = {'before': {'match': source['match'], 'expect_matches': source.get('expect_matches', 1)}}
        elif 'index' in source:
            op['position'] = {'before': {'index': source['index']}}
    return op


class EngineConfig(BaseModel):
    model_config = ConfigDict(extra='allow')
    version: int = 1
    variables: dict[str, Any] = Field(default_factory=dict)
    variable_map: dict[str, dict[str, Any]] | list[dict[str, Any]] = Field(default_factory=dict)
    variable_map_file: str | list[str] | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    documents: dict[str, Any] | list[dict[str, Any]] | None = None
    operations: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode='after')
    def normalize_and_validate(self) -> 'EngineConfig':
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(self.operations):
            if not isinstance(raw, dict):
                raise ValueError(f'operations[{index}] must be a mapping')
            op = {**self.defaults, **normalize_operation(raw)}
            if op.get('op') == 'copy_item' and 'position' not in op:
                default_position = op.pop('copy_item_position', self.defaults.get('copy_item_position', 'before_source'))
                source = op.get('source', {})
                if default_position == 'required':
                    raise ValueError(f'operations[{index}] copy_item requires position')
                if default_position in {'before_source', 'after_source'}:
                    side = 'before' if default_position == 'before_source' else 'after'
                    if 'match' in source:
                        op['position'] = {side: {'match': source['match'], 'expect_matches': source.get('expect_matches', 1)}}
                    elif 'index' in source:
                        op['position'] = {side: {'index': source['index']}}
                elif default_position not in {None, 'none'}:
                    raise ValueError(f'operations[{index}] invalid copy_item_position: {default_position}')
            name = op.get('op')
            if not name:
                raise ValueError(f'operations[{index}] is missing op')
            if name not in SUPPORTED_OPS:
                raise ValueError(f'operations[{index}] unsupported op: {name}')
            if name not in {'capture'} and name not in {'copy_node','move_node','copy_item_to_node'} and 'path' not in op:
                op['path'] = '$'
            if name in {'insert','append','prepend','insert_at','insert_before','insert_after'} and 'value' not in op and 'values' not in op:
                raise ValueError(f'operations[{index}] {name} requires value or values')
            if name in {'copy_item','copy_item_to_node'} and 'source' not in op:
                raise ValueError(f'operations[{index}] {name} requires source/from')
            if name in {'copy_item', 'copy_item_to_node', 'update_item'} and 'item_operations' in op:
                if not isinstance(op['item_operations'], list):
                    raise ValueError(f'operations[{index}] {name}.item_operations must be a list')
                for nested_index, nested in enumerate(op['item_operations']):
                    if not isinstance(nested, dict) or nested.get('op') not in SUPPORTED_OPS:
                        raise ValueError(f'operations[{index}].item_operations[{nested_index}] has invalid op')
            if name in {'update_item','remove_item'} and 'match' not in op and 'name' not in op and 'name_pattern' not in op:
                raise ValueError(f'operations[{index}] {name} requires match/find/name/name_pattern')
            if name == 'move_item' and 'match' not in op and 'source' not in op:
                raise ValueError(f'operations[{index}] move_item requires match/find or source')
            if name == 'insert_key' and 'key' not in op:
                raise ValueError(f'operations[{index}] insert_key requires key')
            if name == 'rename_key' and not {'old_key','new_key'} <= op.keys():
                raise ValueError(f'operations[{index}] rename_key requires old_key and new_key')
            normalized.append(op)
        self.operations = normalized
        normalized_rules: list[dict[str, Any]] = []
        for rule_index, raw_rule in enumerate(self.rules):
            if not isinstance(raw_rule, dict):
                raise ValueError(f'rules[{rule_index}] must be a mapping')
            rule = dict(raw_rule)
            if not rule.get('id'):
                rule['id'] = f'rule-{rule_index + 1}'
            filters = rule.get('filters', {})
            if filters is not None and not isinstance(filters, dict):
                raise ValueError(f'rules[{rule_index}].filters must be a mapping')
            rule_ops = rule.get('operations', [])
            if not isinstance(rule_ops, list) or not rule_ops:
                raise ValueError(f'rules[{rule_index}].operations must be a non-empty list')
            nested_cfg = EngineConfig.model_validate({
                'version': self.version,
                'variables': {**self.variables, **rule.get('variables', {})},
                'variable_map': rule.get('variable_map', {}),
                'options': self.options,
                'defaults': {**self.defaults, **rule.get('defaults', {})},
                'documents': rule.get('documents'),
                'operations': rule_ops,
            })
            rule['operations'] = nested_cfg.operations
            rule['priority'] = int(rule.get('priority', 0))
            rule['stop'] = bool(rule.get('stop', False))
            rule['enabled'] = bool(rule.get('enabled', True))
            normalized_rules.append(rule)
        self.rules = normalized_rules
        return self

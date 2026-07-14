from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import os
import shutil
import tempfile
import fnmatch
import re
from typing import Any
import xml.etree.ElementTree as ET

from jinja2 import Environment, StrictUndefined

from yaml_config_engine.models import EngineConfig
from yaml_config_engine.errors import ValidationError
from yaml_config_engine.variable_scope import resolve_scope_variables
from yaml_config_engine.yamlio import load_one
from yaml_config_engine.config_loader import load_config_with_variable_maps

from .xmltext import (
    Patch, XmlFormatError, XmlTarget, apply_patches, child_indent, detect_newline,
    line_indent, node_text, parse_xml_spans, select, serialize_element,
    xml_escape_attr, xml_escape_text, xml_unescape,
)


@dataclass
class XmlApplyResult:
    input_path: Path
    output_path: Path | None
    changed: bool
    text: str
    applied_operations: list[str] = field(default_factory=list)


def _render(value: Any, context: dict[str, Any]) -> Any:
    env = Environment(undefined=StrictUndefined, autoescape=False)
    if isinstance(value, str):
        if '{{' not in value and '{%' not in value:
            return value
        return env.from_string(value).render(context)
    if isinstance(value, list):
        return [_render(v, context) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, context) for k, v in value.items()}
    return value



def _missing_policy(spec: dict[str, Any], *, allow_create: bool = True) -> str:
    explicit = spec.get('missing')
    legacy_present = 'create_missing' in spec
    legacy = bool(spec.get('create_missing', False))
    if explicit is None:
        zero_policy = str(spec.get('on_zero_matches', '')).lower()
        if zero_policy in {'ignore', 'skip'}:
            policy = 'skip'
        else:
            policy = 'create' if legacy else 'error'
    else:
        policy = str(explicit).lower()
        if policy not in {'error','skip','create'}:
            raise XmlFormatError('missing must be error, skip, or create')
        if legacy_present and ((legacy and policy != 'create') or (not legacy and policy == 'create')):
            raise XmlFormatError('Conflicting missing and create_missing settings')
    if policy == 'create' and not allow_create:
        raise XmlFormatError('missing: create is not supported for pattern selectors')
    return policy


def _name_matches(name: str, pattern: str, mode: str) -> bool:
    if mode == 'regex': return re.search(pattern, name) is not None
    if mode == 'iregex': return re.search(pattern, name, re.I) is not None
    if mode == 'iglob': return fnmatch.fnmatch(name.lower(), pattern.lower())
    return fnmatch.fnmatchcase(name, pattern)


def _expand_named_targets(targets: list[XmlTarget], spec: dict[str, Any]) -> tuple[list[XmlTarget], str | None]:
    exact = spec.get('name', spec.get('key'))
    pattern = spec.get('name_pattern', spec.get('key_pattern'))
    if exact is None and pattern is None:
        return targets, None
    expanded: list[XmlTarget] = []
    if exact is not None:
        for target in targets:
            expanded.extend(XmlTarget('element', child) for child in target.node.direct_children(str(exact)))
        return expanded, str(exact)
    mode = str(spec.get('pattern_type', 'glob')).lower()
    for target in targets:
        expanded.extend(XmlTarget('element', child) for child in target.node.children if _name_matches(child.name, str(pattern), mode))
    return expanded, None

def _expect(targets: list[XmlTarget], spec: dict[str, Any], label: str) -> None:
    expected = spec.get('expect_matches')
    if expected is not None and len(targets) != int(expected):
        raise XmlFormatError(f"{label}: expected {expected} matches, got {len(targets)}")
    if not targets and spec.get('on_zero_matches', 'error') == 'error':
        raise XmlFormatError(f"{label}: no XML node matched {spec.get('path')!r}")
    if len(targets) > 1 and spec.get('on_multiple_matches', 'error') == 'error':
        raise XmlFormatError(f"{label}: multiple XML nodes matched ({len(targets)})")


def _target_value(text: str, target: XmlTarget) -> str:
    if target.kind == 'attribute' and target.attr:
        return text[target.attr.value_start:target.attr.value_end]
    return node_text(text, target.node)


def _item_matches(text: str, node: Any, match: dict[str, Any]) -> bool:
    for key, expected in (match or {}).items():
        if key.startswith('@'):
            attr = node.attrs.get(key[1:])
            actual = text[attr.value_start:attr.value_end] if attr else None
        elif key in {'#text', 'text', 'value'}:
            actual = node_text(text, node)
        else:
            children = node.direct_children(key)
            actual = node_text(text, children[0]) if children else None
        if isinstance(expected, dict) and '$pattern' in expected:
            if actual is None or not _name_matches(str(actual), str(expected['$pattern']), str(expected.get('$pattern_type','glob'))):
                return False
        elif str(actual) != str(expected):
            return False
    return True


def _item_nodes(text: str, containers: list[XmlTarget], spec: dict[str, Any]) -> list[Any]:
    element = spec.get('element') or spec.get('item_element') or '*'
    result=[]
    for target in containers:
        candidates = target.node.direct_children(element)
        result.extend([n for n in candidates if _item_matches(text, n, spec.get('match', {}))])
    return result


def _relative_parts(key: str) -> list[str]:
    normalized = str(key).strip().replace('/', '.')
    return [part for part in normalized.split('.') if part]


def _relative_value(text: str, node: Any, key: str) -> str | None:
    if key.startswith('@'):
        attr = node.attrs.get(key[1:])
        return xml_unescape(text[attr.value_start:attr.value_end]) if attr else None
    if key in {'#text', 'text', 'value'}:
        return node_text(text, node)
    current = node
    for part in _relative_parts(key):
        children = current.direct_children(part)
        if not children:
            return None
        current = children[0]
    return node_text(text, current)


def _relative_path(root_name: str, key: str) -> str:
    if key.startswith('@'):
        return f'/{root_name}/{key}'
    if key in {'#text', 'text'}:
        return f'/{root_name}'
    return f'/{root_name}/' + '/'.join(_relative_parts(key))


class XmlPatchEngine:
    """Text-preserving XML patch engine.

    Unmodified byte ranges are copied verbatim. It never serializes the whole
    document, so comments, indentation, attribute order, quote style, XML
    declaration, namespace prefixes, blank lines and line endings remain intact.
    """

    def load_config(self, path: str | Path) -> EngineConfig:
        return EngineConfig.model_validate(load_config_with_variable_maps(path))

    def apply_text(self, text: str, config: EngineConfig | dict[str, Any], variables: dict[str, Any] | None = None) -> tuple[str, list[str]]:
        cfg = config if isinstance(config, EngineConfig) else EngineConfig.model_validate(config)
        current = text
        applied: list[str] = []
        context_vars = {**cfg.variables, **(variables or {})}
        captures: dict[str, Any] = {}
        for raw in cfg.operations:
            context = {**context_vars, **captures, 'captures': captures}
            spec = _render(deepcopy(raw), context)
            op = spec['op']
            if op == 'capture':
                root, _ = parse_xml_spans(current)
                targets = select(current, root, spec.get('path', '/'))
                _expect(targets, spec, 'capture')
                captures[spec.get('as', spec.get('id', 'capture'))] = _target_value(current, targets[0])
                applied.append(spec.get('id', op)); continue
            current = self._apply_operation(current, spec)
            applied.append(spec.get('id', op))
        return current, applied

    def _apply_operation(self, text: str, spec: dict[str, Any]) -> str:
        root, _ = parse_xml_spans(text)
        op = spec['op']
        path = spec.get('path', '/')
        targets = select(text, root, path)

        if op == 'copy_item_to_node':
            source_path = spec.get('from_path') or spec.get('path')
            target_path = spec.get('to_path') or spec.get('target_path')
            if not source_path or not target_path:
                raise XmlFormatError('copy_item_to_node requires from_path/path and to_path')
            source_containers = select(text, root, source_path)
            destinations = select(text, root, target_path)
            _expect(source_containers, {'path': source_path, 'on_multiple_matches': 'error'}, op + '.source-container')
            _expect(destinations, {'path': target_path, 'on_multiple_matches': 'error'}, op + '.target')
            container = source_containers[0].node
            source_spec = spec.get('source', {})
            item_name = spec.get('element') or spec.get('item_element') or '*'
            candidates = container.direct_children(item_name)
            if 'index' in source_spec:
                idx = int(source_spec['index'])
                items = [candidates[idx]] if -len(candidates) <= idx < len(candidates) else []
            else:
                items = [n for n in candidates if _item_matches(text, n, source_spec.get('match', {}))]
            expected = source_spec.get('expect_matches')
            if expected is not None and len(items) != int(expected):
                raise XmlFormatError(f'copy_item_to_node: expected {expected} source items, got {len(items)}')
            if len(items) != 1:
                raise XmlFormatError(f'copy_item_to_node: expected exactly one source item, got {len(items)}')
            source = items[0]
            snippet = text[source.start:source.end]
            snippet = self._apply_item_changes(snippet, source.name, spec.get('set', spec.get('overrides', {})), spec.get('remove', []))
            duplicate = spec.get('duplicate') or {}
            unique_by = duplicate.get('unique_by', [])
            if isinstance(unique_by, str):
                unique_by = [unique_by]
            destination = destinations[0].node
            if unique_by:
                clone_root, _ = parse_xml_spans(snippet)
                clone_values = tuple(_relative_value(snippet, clone_root, key) for key in unique_by)
                destination_candidates = destination.direct_children(source.name)
                found_duplicate = any(tuple(_relative_value(text, candidate, key) for key in unique_by) == clone_values for candidate in destination_candidates)
                if found_duplicate:
                    policy = duplicate.get('policy', 'error')
                    if policy == 'skip':
                        return text
                    if policy == 'error':
                        raise XmlFormatError(f'copy_item_to_node: duplicate item by {unique_by}')
            return apply_patches(text, [self._raw_at_position(text, destination, snippet, spec.get('position'))])

        if op in {'set', 'replace'}:
            selector_parent_targets = targets
            targets, exact_selector_name = _expand_named_targets(targets, spec)
            if any(k in spec for k in ('name_pattern','key_pattern')) and 'on_multiple_matches' not in spec: spec['on_multiple_matches'] = 'all'
            policy = _missing_policy(spec, allow_create=exact_selector_name is not None or not any(k in spec for k in ('name_pattern','key_pattern')))
            if not targets:
                if policy == 'skip': return text
                if policy == 'create':
                    if exact_selector_name is not None:
                        _expect(selector_parent_targets, {'path': path, 'on_multiple_matches': 'error'}, op + '.selector-parent')
                        return apply_patches(text, [self._insert_child_patch(text, selector_parent_targets[0].node, exact_selector_name, spec.get('value'), spec.get('position'))])
                    if '/@' in path:
                        return self._create_missing_attribute(text, root, path, spec.get('value'))
                    # Support the common enterprise case where a predicate or
                    # wildcard uniquely selects an existing parent and only
                    # the final child section is missing, for example:
                    # /components/component[@id='c050']/runtime.
                    parent_path, separator, child_name = path.rpartition('/')
                    if (separator and parent_path and
                            re.fullmatch(r'[A-Za-z_][\w:.-]*', child_name or '')):
                        parents = select(text, root, parent_path)
                        if parents:
                            parent_expect = {
                                'path': parent_path,
                                'on_multiple_matches': spec.get('on_multiple_matches', 'error'),
                                'on_zero_matches': 'error',
                            }
                            _expect(parents, parent_expect, op + '.create-parent')
                            patches = [
                                self._insert_child_patch(
                                    text, parent.node, child_name, spec.get('value'),
                                    spec.get('position') or {'last': True},
                                )
                                for parent in parents
                            ]
                            return apply_patches(text, patches)
                    return self._create_missing(text, root, path, spec.get('value'))
            _expect(targets, spec, op)
            value = spec.get('value')
            patches: list[Patch] = []
            for t in targets:
                if t.kind == 'attribute' and t.attr:
                    patches.append(Patch(t.attr.value_start, t.attr.value_end, xml_escape_attr(value, t.attr.quote), op))
                elif t.kind in {'text', 'element'}:
                    node = t.node
                    if isinstance(value, (dict, list)):
                        replacement = self._serialize_content(value, node, text)
                    else:
                        replacement = xml_escape_text(value)
                    if node.self_closing:
                        start_raw = text[node.start:node.start_tag_end]
                        open_raw = start_raw[:-2].rstrip() + '>'
                        replacement = open_raw + replacement + f'</{node.name}>'
                        patches.append(Patch(node.start, node.end or node.start_tag_end, replacement, op))
                    else:
                        patches.append(Patch(node.content_start, node.content_end, replacement, op))
            return apply_patches(text, patches)

        if op == 'remove':
            selector_parent_targets = targets
            targets, exact_selector_name = _expand_named_targets(targets, spec)
            if any(k in spec for k in ('name_pattern','key_pattern')) and 'on_multiple_matches' not in spec: spec['on_multiple_matches'] = 'all'
            policy = _missing_policy(spec, allow_create=False)
            if not targets and policy == 'skip': return text
            _expect(targets, spec, op)
            patches = []
            for t in targets:
                if t.kind == 'attribute' and t.attr:
                    start = t.attr.name_start
                    while start > t.node.open_name_end and text[start-1] in ' \t\r\n':
                        start -= 1
                    patches.append(Patch(start, t.attr.value_end + 1, '', op))
                elif t.kind == 'text':
                    patches.append(Patch(t.node.content_start, t.node.content_end, '', op))
                else:
                    start, end = self._element_removal_span(text, t.node)
                    patches.append(Patch(start, end, '', op))
            return apply_patches(text, patches)

        if op == 'rename_key':
            parent_targets = targets
            _expect(parent_targets, spec, op)
            old, new = spec['old_key'], spec['new_key']
            patches = []
            matches = []
            for pt in parent_targets:
                for child in pt.node.direct_children(old):
                    matches.append(child)
                    patches.append(Patch(child.open_name_start, child.open_name_end, new, op))
                    if child.close_name_start is not None:
                        patches.append(Patch(child.close_name_start, child.close_name_end or child.close_name_start, new, op))
            if not matches:
                raise XmlFormatError(f"rename_key: child element {old!r} not found")
            return apply_patches(text, patches)

        if op == 'insert_key':
            _expect(targets, spec, op)
            patches = []
            for t in targets:
                patches.append(self._insert_child_patch(text, t.node, spec['key'], spec.get('value'), spec.get('position')))
            return apply_patches(text, patches)

        if op in {'append', 'prepend', 'insert', 'insert_at', 'insert_before', 'insert_after'}:
            _expect(targets, spec, op)
            patches = []
            values = spec.get('values', [spec.get('value')])
            item_name = spec.get('element') or spec.get('key') or 'item'
            for t in targets:
                pos = dict(spec.get('position') or {})
                if op == 'append':
                    pos = {'last': True}
                elif op == 'prepend':
                    pos = {'first': True}
                elif op == 'insert_at':
                    pos = {'index': spec['index']}
                elif op in {'insert_before', 'insert_after'}:
                    matches = [n for n in t.node.direct_children(item_name) if _item_matches(text, n, spec.get('match', {}))]
                    expected = spec.get('expect_matches')
                    if expected is not None and len(matches) != int(expected):
                        raise XmlFormatError(f'{op}: expected {expected} position matches, got {len(matches)}')
                    if len(matches) != 1:
                        raise XmlFormatError(f'{op}: expected exactly one position match, got {len(matches)}')
                    children = t.node.children
                    index = children.index(matches[0]) + (1 if op == 'insert_after' else 0)
                    pos = {'index': index}
                patches.append(self._insert_children_patch(text, t.node, item_name, values, pos))
            return apply_patches(text, patches)

        if op in {'update_item', 'upsert_item'}:
            _expect(targets, spec, op + '.container')
            match_spec = dict(spec)
            if 'name' in spec and 'name' not in match_spec.get('match', {}): match_spec['match'] = {**match_spec.get('match', {}), 'name': spec['name']}
            if 'name_pattern' in spec and 'name' not in match_spec.get('match', {}): match_spec['match'] = {**match_spec.get('match', {}), 'name': {'$pattern': spec['name_pattern'], '$pattern_type': spec.get('pattern_type','glob')}}
            items = _item_nodes(text, targets, match_spec)
            if not items and op == 'upsert_item':
                element = spec.get('element') or spec.get('item_element') or 'item'
                value = spec.get('value', spec.get('set', {}))
                return apply_patches(text, [self._insert_child_patch(text, targets[0].node, element, value, spec.get('position'))])
            expected = spec.get('expect_matches')
            if expected is not None and len(items) != int(expected):
                raise XmlFormatError(f'{op}: expected {expected} items, got {len(items)}')
            if len(items) > 1 and spec.get('on_multiple_matches', 'error') == 'error':
                raise XmlFormatError(f'{op}: multiple matching XML items ({len(items)})')
            if not items:
                policy = _missing_policy(spec, allow_create=op == 'upsert_item')
                if policy == 'skip': return text
                if policy == 'create' and op == 'upsert_item':
                    element = spec.get('element') or spec.get('item_element') or 'item'
                    value = spec.get('value', spec.get('set', {}))
                    return apply_patches(text, [self._insert_child_patch(text, targets[0].node, element, value, spec.get('position'))])
                raise XmlFormatError(f'{op}: no matching XML item')
            patches: list[Patch] = []
            for item in items:
                snippet = text[item.start:item.end]
                updated = self._apply_item_changes(snippet, item.name, spec.get('set', {}), spec.get('remove', []))
                patches.append(Patch(item.start, item.end, updated, op))
            return apply_patches(text, patches)

        if op == 'remove_item':
            _expect(targets, spec, op + '.container')
            items=_item_nodes(text,targets,spec)
            expected = spec.get('expect_matches')
            if expected is not None and len(items) != int(expected):
                raise XmlFormatError(f'remove_item: expected {expected} items, got {len(items)}')
            if len(items) > 1 and spec.get('on_multiple_matches', 'error') == 'error':
                raise XmlFormatError(f'remove_item: multiple matching XML items ({len(items)})')
            if not items:
                policy = _missing_policy(spec, allow_create=False)
                if policy == 'skip': return text
                raise XmlFormatError('remove_item: no matching XML item')
            patches=[]
            for item in items:
                st,en=self._element_removal_span(text,item,remove_leading_comment=spec.get('remove_leading_comments', False))
                patches.append(Patch(st,en,'',op))
            return apply_patches(text,patches)

        if op in {'copy_item','move_item'}:
            _expect(targets,spec,op+'.container')
            container=targets[0].node
            source_spec=spec.get('source') or ({'match': spec.get('match', {})} if spec.get('match') is not None else {})
            candidates=container.direct_children(spec.get('element') or spec.get('item_element') or '*')
            if 'index' in source_spec:
                idx=int(source_spec['index']); items=[candidates[idx]] if -len(candidates)<=idx<len(candidates) else []
            else:
                items=[n for n in candidates if _item_matches(text,n,source_spec.get('match',{}))]
            if len(items)!=1: raise XmlFormatError(f'{op}: expected exactly one source item, got {len(items)}')
            source=items[0]
            snippet=text[source.start:source.end]
            if op == 'copy_item':
                snippet = self._apply_item_changes(snippet, source.name, spec.get('set', spec.get('overrides', {})), spec.get('remove', []))
                duplicate = spec.get('duplicate') or {}
                unique_by = duplicate.get('unique_by', [])
                if isinstance(unique_by, str):
                    unique_by = [unique_by]
                if unique_by:
                    clone_root, _ = parse_xml_spans(snippet)
                    clone_values = tuple(_relative_value(snippet, clone_root, key) for key in unique_by)
                    found_duplicate = any(tuple(_relative_value(text, candidate, key) for key in unique_by) == clone_values for candidate in candidates)
                    if found_duplicate:
                        policy = duplicate.get('policy', 'error')
                        if policy == 'skip':
                            return text
                        if policy == 'error':
                            raise XmlFormatError(f'copy_item: duplicate item by {unique_by}')
                pos = self._resolve_item_position(text, container, spec.get('position'), spec.get('element') or spec.get('item_element') or '*')
                return apply_patches(text, [self._raw_at_position(text,container,snippet,pos)])
            pos = self._resolve_item_position(text, container, spec.get('position'), spec.get('element') or spec.get('item_element') or '*')
            source_index = container.children.index(source)
            target_index = int(pos.get('index', len(container.children))) if pos else len(container.children)
            if source_index < target_index:
                target_index -= 1
            st,en=self._element_removal_span(text,source)
            intermediate = apply_patches(text, [Patch(st,en,'',op)])
            root2, _ = parse_xml_spans(intermediate)
            containers2 = select(intermediate, root2, path)
            _expect(containers2, {'path': path, 'on_multiple_matches': 'error'}, op + '.container.after-remove')
            return apply_patches(intermediate, [self._raw_at_position(intermediate, containers2[0].node, snippet, {'index': target_index})])

        if op in {'copy_node', 'move_node'}:
            source_path = spec.get('from_path') or spec.get('source')
            target_path = spec.get('to_path') or spec.get('path')
            sources = select(text, root, source_path)
            destinations = select(text, root, target_path)
            _expect(sources, {**spec, 'path': source_path}, op + '.source')
            _expect(destinations, {**spec, 'path': target_path}, op + '.target')
            source = sources[0].node
            snippet = text[source.start:source.end]
            if op == 'copy_node':
                return apply_patches(text, [self._insert_raw_patch(text, destinations[0].node, snippet, spec.get('position'))])
            start, end = self._element_removal_span(text, source)
            intermediate = apply_patches(text, [Patch(start, end, '', op)])
            root2, _ = parse_xml_spans(intermediate)
            destinations2 = select(intermediate, root2, target_path)
            _expect(destinations2, {**spec, 'path': target_path}, op + '.target.after-remove')
            return apply_patches(intermediate, [self._insert_raw_patch(intermediate, destinations2[0].node, snippet, spec.get('position'))])

        if op in {'copy_key', 'move_key'}:
            _expect(targets, spec, op)
            parent = targets[0].node
            candidates = parent.direct_children(spec['source_key'])
            if len(candidates) != 1:
                raise XmlFormatError(f"{op}: expected one child named {spec['source_key']!r}, got {len(candidates)}")
            source = candidates[0]
            snippet = text[source.start:source.end]
            target_key = spec.get('target_key', source.name)
            if target_key != source.name:
                snippet = snippet.replace(f'<{source.name}', f'<{target_key}', 1)
                close_at = snippet.rfind(f'</{source.name}>')
                if close_at >= 0:
                    snippet = snippet[:close_at] + f'</{target_key}>' + snippet[close_at+len(source.name)+3:]
            if op == 'copy_key':
                return apply_patches(text, [self._insert_raw_patch(text, parent, snippet, spec.get('position'))])
            start, end = self._element_removal_span(text, source)
            intermediate = apply_patches(text, [Patch(start, end, '', op)])
            root2, _ = parse_xml_spans(intermediate)
            parents2 = select(intermediate, root2, path)
            _expect(parents2, {'path': path, 'on_multiple_matches': 'error'}, op + '.parent.after-remove')
            return apply_patches(intermediate, [self._insert_raw_patch(intermediate, parents2[0].node, snippet, spec.get('position'))])

        if op == 'merge':
            selector_parent_targets = targets
            targets, exact_selector_name = _expand_named_targets(targets, spec)
            if any(k in spec for k in ('name_pattern','key_pattern')) and 'on_multiple_matches' not in spec: spec['on_multiple_matches'] = 'all'
            policy = _missing_policy(spec, allow_create=exact_selector_name is not None or not any(k in spec for k in ('name_pattern','key_pattern')))
            value = spec.get('value')
            if not targets:
                if policy == 'skip': return text
                if policy == 'create' and exact_selector_name is not None:
                    _expect(selector_parent_targets, {'path': path, 'on_multiple_matches': 'error'}, op + '.selector-parent')
                    return apply_patches(text, [self._insert_child_patch(text, selector_parent_targets[0].node, exact_selector_name, value, spec.get('position'))])
                if policy == 'create':
                    return self._create_missing(text, root, path, value)
            _expect(targets, spec, op)
            if not isinstance(value, dict):
                raise XmlFormatError('XML merge requires a mapping value')
            result = text
            # Reparse after each key so offsets remain valid and existing formatting is retained.
            for key, child_value in value.items():
                root2, _ = parse_xml_spans(result)
                pts = select(result, root2, path)
                parent = pts[0].node
                children = parent.direct_children(key)
                if children:
                    child_path = path.rstrip('/') + '/' + key
                    result = self._apply_operation(result, {'op':'set','path':child_path,'value':child_value,'on_multiple_matches':'error'})
                else:
                    result = apply_patches(result, [self._insert_child_patch(result, parent, key, child_value, {'last':True})])
            return result

        raise XmlFormatError(f"XML operation not supported: {op}")

    def _apply_item_changes(self, snippet: str, root_name: str, values: dict[str, Any], removals: list[str]) -> str:
        result = snippet
        for key, value in (values or {}).items():
            result = self._apply_operation(result, {
                'op': 'set', 'path': _relative_path(root_name, key), 'value': value,
                'create_missing': True, 'on_multiple_matches': 'error',
            })
        for key in removals or []:
            path = _relative_path(root_name, key)
            root, _ = parse_xml_spans(result)
            if select(result, root, path):
                result = self._apply_operation(result, {'op': 'remove', 'path': path, 'on_zero_matches': 'ignore', 'on_multiple_matches': 'error'})
        return result

    @staticmethod
    def _resolve_item_position(text: str, parent: Any, position: dict[str, Any] | None, element: str) -> dict[str, Any]:
        pos = dict(position or {})
        if pos.get('first') is True:
            return {'index': 0}
        if pos.get('last') is True or not pos:
            return {'index': len(parent.children)}
        if 'index' in pos:
            return {'index': max(0, min(int(pos['index']), len(parent.children)))}
        if 'before_key' in pos:
            index = next((i for i, child in enumerate(parent.children) if child.name == pos['before_key']), len(parent.children))
            return {'index': index}
        if 'after_key' in pos:
            index = next((i + 1 for i, child in enumerate(parent.children) if child.name == pos['after_key']), len(parent.children))
            return {'index': index}
        for side in ('before', 'after'):
            if side not in pos:
                continue
            target = pos[side] or {}
            if 'index' in target:
                index = int(target['index'])
            else:
                matches = [n for n in parent.direct_children(element) if _item_matches(text, n, target.get('match', {}))]
                expected = target.get('expect_matches')
                if expected is not None and len(matches) != int(expected):
                    raise XmlFormatError(f'position.{side}: expected {expected} matches, got {len(matches)}')
                if len(matches) != 1:
                    raise XmlFormatError(f'position.{side}: expected exactly one match, got {len(matches)}')
                index = parent.children.index(matches[0])
            return {'index': index + (1 if side == 'after' else 0)}
        raise XmlFormatError(f'Unsupported XML item position: {position}')

    @staticmethod
    def _serialize_content(value: Any, node: Any, text: str) -> str:
        newline = detect_newline(text)
        indent = child_indent(text, node)
        if isinstance(value, list):
            return ''.join(newline + indent + serialize_element('item', v, indent, newline) for v in value) + newline + line_indent(text, node.start)
        if isinstance(value, dict):
            return ''.join(newline + indent + serialize_element(k, v, indent, newline) for k, v in value.items()) + newline + line_indent(text, node.start)
        return xml_escape_text(value)

    def _create_missing_attribute(self, text: str, root: Any, path: str, value: Any) -> str:
        parent_path, attr_name = path.rsplit('/@', 1)
        parents = select(text, root, parent_path)
        _expect(parents, {'path': parent_path, 'on_multiple_matches': 'all'}, 'create_missing.attribute.parent')
        patches = []
        for parent in parents:
            node = parent.node
            insert_at = node.start_tag_end - (2 if node.self_closing else 1)
            patches.append(Patch(insert_at, insert_at, f' {attr_name}="{xml_escape_attr(value)}"', 'set'))
        return apply_patches(text, patches)

    def _create_missing(self, text: str, root: Any, path: str, value: Any) -> str:
        clean = path.strip('/').split('/')
        if clean and clean[0] == root.name:
            clean = clean[1:]
        if not clean or any('[' in p or p.startswith('@') for p in clean):
            raise XmlFormatError(f"create_missing supports simple element paths only: {path}")
        current = root
        consumed = '/' + root.name
        for part in clean[:-1]:
            children = current.direct_children(part)
            if not children:
                nested: Any = value
                for name in reversed(clean[clean.index(part)+1:]):
                    nested = {name: nested}
                return apply_patches(text, [self._insert_child_patch(text, current, part, nested, {'last':True})])
            current = children[0]
            consumed += '/' + part
        return apply_patches(text, [self._insert_child_patch(text, current, clean[-1], value, {'last':True})])

    @staticmethod
    def _element_removal_span(text: str, node: Any, remove_leading_comment: bool = False) -> tuple[int, int]:
        start, end = node.start, node.end or node.start_tag_end
        node_line_start = text.rfind('\n', 0, start) + 1
        removal_start = node_line_start if text[node_line_start:start].strip() == '' else start
        if remove_leading_comment:
            prefix = text[:removal_start]
            comment_end = prefix.rfind('-->')
            if comment_end >= 0 and prefix[comment_end + 3:].strip() == '':
                comment_start = prefix.rfind('<!--', 0, comment_end)
                if comment_start >= 0:
                    comment_line_start = prefix.rfind('\n', 0, comment_start) + 1
                    if prefix[comment_line_start:comment_start].strip() == '':
                        removal_start = comment_line_start
        line_end = text.find('\n', end)
        if line_end >= 0 and text[end:line_end].strip() == '':
            return removal_start, line_end + 1
        return removal_start, end

    def _insert_child_patch(self, text: str, parent: Any, name: str, value: Any, position: dict[str, Any] | None) -> Patch:
        return self._insert_children_patch(text, parent, name, [value], position)

    def _insert_children_patch(self, text: str, parent: Any, name: str, values: list[Any], position: dict[str, Any] | None) -> Patch:
        newline = detect_newline(text)
        indent = child_indent(text, parent)
        parent_indent = line_indent(text, parent.start)
        rendered = [serialize_element(name, v, indent, newline) for v in values]
        block = (newline + indent).join(rendered)
        pos = position or {'last': True}
        children = parent.children
        if parent.self_closing:
            start_raw = text[parent.start:parent.start_tag_end]
            open_raw = start_raw[:-2].rstrip() + '>'
            replacement = open_raw + newline + indent + block + newline + parent_indent + f'</{parent.name}>'
            return Patch(parent.start, parent.end or parent.start_tag_end, replacement, 'insert')
        index = len(children)
        if pos.get('first') is True:
            index = 0
        elif 'index' in pos:
            index = max(0, min(int(pos['index']), len(children)))
        elif 'before_key' in pos:
            index = next((i for i,c in enumerate(children) if c.name == pos['before_key']), len(children))
        elif 'after_key' in pos:
            index = next((i+1 for i,c in enumerate(children) if c.name == pos['after_key']), len(children))
        if children and index < len(children):
            offset = children[index].start
            return Patch(offset, offset, block + newline + indent, 'insert')
        existing = text[parent.content_start:parent.content_end]
        if children:
            offset = children[-1].end or children[-1].start_tag_end
            return Patch(offset, offset, newline + indent + block, 'insert')
        if existing.strip() == '':
            replacement = newline + indent + block + newline + parent_indent
            return Patch(parent.content_start, parent.content_end, replacement, 'insert')
        return Patch(parent.content_end, parent.content_end, newline + indent + block + newline + parent_indent, 'insert')

    def _insert_raw_patch(self, text: str, parent: Any, snippet: str, position: dict[str, Any] | None) -> Patch:
        newline = detect_newline(text)
        indent = child_indent(text, parent)
        source_indent = line_indent(text, snippet and 0 or 0)
        lines = snippet.splitlines()
        if len(lines) > 1:
            minimum = min((len(line)-len(line.lstrip(' \t')) for line in lines[1:] if line.strip()), default=0)
            snippet = lines[0].lstrip() + ''.join(newline + indent + line[minimum:] for line in lines[1:])
        return self._insert_children_patch(text, parent, '__RAW__', [], position)._replace if False else self._raw_at_position(text, parent, snippet, position)

    def _raw_at_position(self, text: str, parent: Any, snippet: str, position: dict[str, Any] | None) -> Patch:
        newline = detect_newline(text); indent = child_indent(text, parent); parent_indent = line_indent(text, parent.start)
        if parent.self_closing:
            start_raw = text[parent.start:parent.start_tag_end]
            open_raw = start_raw[:-2].rstrip() + '>'
            replacement = open_raw + newline + indent + snippet + newline + parent_indent + f'</{parent.name}>'
            return Patch(parent.start, parent.end or parent.start_tag_end, replacement, 'copy')
        pos = position or {'last':True}; children = parent.children; index = len(children)
        if pos.get('first') is True: index = 0
        elif 'index' in pos: index = max(0, min(int(pos['index']), len(children)))
        elif 'before_key' in pos: index = next((i for i,c in enumerate(children) if c.name == pos['before_key']), len(children))
        elif 'after_key' in pos: index = next((i+1 for i,c in enumerate(children) if c.name == pos['after_key']), len(children))
        if children and index < len(children):
            offset = children[index].start
            return Patch(offset, offset, snippet + newline + indent, 'copy')
        if children:
            offset = children[-1].end or children[-1].start_tag_end
            return Patch(offset, offset, newline + indent + snippet, 'copy')
        if text[parent.content_start:parent.content_end].strip() == '':
            return Patch(parent.content_start, parent.content_end, newline + indent + snippet + newline + parent_indent, 'copy')
        return Patch(parent.content_end, parent.content_end, newline + indent + snippet + newline + parent_indent, 'copy')

    def apply_file(self, source: str | Path, config: EngineConfig | dict[str, Any] | str | Path,
                   output: str | Path | None = None, variables: dict[str, Any] | None = None,
                   dry_run: bool | None = None) -> XmlApplyResult:
        source = Path(source)
        cfg = self.load_config(config) if isinstance(config, (str, Path)) else (config if isinstance(config, EngineConfig) else EngineConfig.model_validate(config))
        original_bytes = source.read_bytes()
        bom = original_bytes.startswith(b'\xef\xbb\xbf')
        text = original_bytes.decode('utf-8-sig')
        extra = cfg.model_extra or {}
        if extra.get('xml_action') == 'replace_entire_file':
            updated = str(extra.get('xml_exact_text', ''))
            bom = bool(extra.get('xml_utf8_bom', bom))
            applied = ['replace-entire-file-exact']
        else:
            updated, applied = self.apply_text(text, cfg, variables)
        output_cfg = cfg.options.get('xml_output', {}) if isinstance(cfg.options.get('xml_output', {}), dict) else {}
        line_ending = output_cfg.get('line_ending', cfg.options.get('line_ending', 'preserve'))
        if line_ending not in {'preserve', 'lf', 'crlf'}:
            raise ValidationError('options.xml_output.line_ending must be preserve, lf, or crlf')
        if line_ending != 'preserve':
            normalized = updated.replace('\r\n', '\n').replace('\r', '\n')
            updated = normalized.replace('\n', '\r\n') if line_ending == 'crlf' else normalized
        changed = updated != text or (bool(original_bytes.startswith(b'\xef\xbb\xbf')) != bom)
        effective_dry_run = cfg.options.get('dry_run', False) if dry_run is None else dry_run
        output_path = Path(output) if output else source
        if changed and not effective_dry_run:
            # Validate syntax without formatting/serializing it.
            ET.fromstring(updated)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if cfg.options.get('backup', False) and output_path.exists():
                shutil.copy2(output_path, output_path.with_suffix(output_path.suffix + '.bak'))
            payload = updated.encode('utf-8')
            if bom: payload = b'\xef\xbb\xbf' + payload
            if cfg.options.get('atomic_write', True):
                fd, tmp = tempfile.mkstemp(prefix=output_path.name, dir=str(output_path.parent))
                os.close(fd)
                try:
                    Path(tmp).write_bytes(payload)
                    os.replace(tmp, output_path)
                finally:
                    if os.path.exists(tmp): os.unlink(tmp)
            else:
                output_path.write_bytes(payload)
        return XmlApplyResult(source, None if effective_dry_run else output_path, changed, updated, applied)

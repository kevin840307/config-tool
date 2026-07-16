from __future__ import annotations
from dataclasses import dataclass
from copy import deepcopy
import re
import os
import time
import json
from typing import Any
from .engine import YamlPatchEngine
from .yamlio import clone
from .models import omit_concise_defaults
from .comparison import strict_equal, strict_yaml_equal
from .pathing import parse_path, expand_paths

@dataclass
class CompileResult:
    config: dict[str, Any]
    verified: bool
    strategy: str
    warnings: list[str]

class DiffCompiler:
    ID_KEYS = ('name','id','key','code','version')

    @staticmethod
    def _quote_style(value: Any) -> str:
        name = type(value).__name__
        if name == 'SingleQuotedScalarString': return 'single'
        if name == 'DoubleQuotedScalarString': return 'double'
        return 'plain'

    @classmethod
    def _replace_scalar_op(cls, path: str, value: Any, previous: Any | None = None) -> dict[str, Any]:
        op: dict[str, Any] = {'op': 'replace', 'path': path, 'value': deepcopy(value)}
        if isinstance(value, str) and isinstance(previous, str) and cls._quote_style(value) != cls._quote_style(previous):
            op['quote'] = cls._quote_style(value)
        return op

    def __init__(self, identity_rules: dict[str, list[str]] | None = None, *, retry_protection: bool = False, readable: bool = True, optimization_timeout_seconds: float | None = None, optimization_max_candidates: int | None = None) -> None:
        self.identity_rules = identity_rules or {}
        self.retry_protection = retry_protection
        self.readable = readable
        self.optimization_timeout_seconds = float(optimization_timeout_seconds if optimization_timeout_seconds is not None else os.getenv('CONFIG_TOOL_OPTIMIZATION_TIMEOUT_SECONDS', '5'))
        self.optimization_max_candidates = int(optimization_max_candidates if optimization_max_candidates is not None else os.getenv('CONFIG_TOOL_OPTIMIZATION_MAX_CANDIDATES', '2000'))
        self._optimization_deadline = 0.0
        self._optimization_candidates = 0
        self._optimization_limit_reported = False
        self._replay_cache: dict[Any, bool] = {}
        self._replay_cache_hits = 0
        self.large_mode_threshold = int(os.getenv('CONFIG_TOOL_LARGE_MODE_THRESHOLD', '500'))
        self.large_mode_max_replays = int(os.getenv('CONFIG_TOOL_LARGE_MODE_MAX_REPLAYS', '40'))
        self._optimization_session_active = False
        self._large_mode_replays = 0
        self.warnings: list[str] = []

    def compile(self, before: Any, after: Any, variables: dict[str, Any] | None = None) -> CompileResult:
        self.warnings = []
        self._replay_cache = {}
        self._replay_cache_hits = 0
        self._optimization_candidates = 0
        self._optimization_limit_reported = False
        self._large_mode_replays = 0
        self._optimization_deadline = time.monotonic() + max(0.05, self.optimization_timeout_seconds)
        self._optimization_session_active = True
        ops: list[dict[str, Any]] = []
        try:
            self._diff(before, after, '$', ops)
        except Exception as exc:
            self.warnings.append(f'Semantic compiler fallback after {type(exc).__name__}: {exc}')
            ops = [{'id': 'replace-document', 'op': 'replace', 'path': '$', 'value': deepcopy(after)}]
        ops = self._optimize_selectors(before, after, ops)
        if self.readable:
            candidate = self._make_readable(ops)
            if candidate == ops:
                pass
            elif self._candidate_replays(before, after, candidate) and self._is_better(candidate, ops):
                ops = candidate
            else:
                self.warnings.append('Readable config candidate failed replay or did not improve readability and was rolled back.')

            # Readability lowering can expose new semantic equality. For example,
            # one generated update_item may still contain item_operations while a
            # sibling has already been expressed with set/remove shorthand. After
            # _make_readable both operations are equivalent, but the first optimizer
            # pass has already finished. Run one bounded post-readable fixed-point
            # pass so outer paths such as p1/p2 can still collapse to *.
            post_readable = self._optimize_selectors(before, after, ops)
            if post_readable != ops and self._candidate_replays(before, after, post_readable):
                ops = post_readable
                final_readable = self._make_readable(ops)
                if final_readable != ops and self._candidate_replays(before, after, final_readable) and self._is_better(final_readable, ops):
                    ops = final_readable
        # Large inputs may consume the optional optimizer budget before the
        # outer semantic groups are reached. Run one bounded, guaranteed outer
        # checkpoint from the latest verified state; it never restarts from the
        # original operations and performs at most a small number of fast replays.
        # Always run the bounded final semantic outer checkpoint when there
        # are enough operations to benefit. Complex Helm values may produce
        # hundreds (but fewer than the large-mode threshold) and otherwise
        # miss the final wildcard/path-set collapse after the main budget is
        # exhausted. This pass is independent, bounded and replay-verified.
        if len(ops) >= 8:
            ops = self._final_large_outer_checkpoint(before, after, ops)

        # ``paths`` is only a precise fallback. Re-check it after every other
        # lowering step because inner optimization may expose a safe single
        # wildcard/list-wildcard, exact union, or a much smaller set of patterns.
        compressed_paths = self._optimize_paths_to_single_path(before, after, ops, final_pass=True)
        if compressed_paths != ops and self._candidate_replays(before, after, compressed_paths):
            ops = compressed_paths

        # Final low-risk readability cleanup. These passes do not reorder arbitrary
        # operations: they only merge equivalent updates on the exact same target
        # and remove paths entries already covered by another selector.
        same_target = self._optimize_same_target_updates(before, after, ops)
        if same_target != ops and self._candidate_replays(before, after, same_target):
            ops = same_target
        reduced_paths = self._remove_redundant_paths(before, after, ops)
        if reduced_paths != ops and self._candidate_replays(before, after, reduced_paths):
            ops = reduced_paths
        # Optimizers may reintroduce explicit safety fields after the readable pass.
        # Run one final profile-aware cleanup and accept it only after replay.
        concise_ops = omit_concise_defaults(ops)
        if concise_ops != ops and self._candidate_replays(before, after, concise_ops):
            ops = concise_ops
        wildcard_count = sum(1 for op in ops if isinstance(op.get('path'), str) and ('*' in op['path'] or '[*]' in op['path']))
        paths_count = sum(1 for op in ops if isinstance(op.get('paths'), list))
        variable_count = repr(ops).count('{{')
        self.warnings.append(
            f'Auto config quality: operations={len(ops)}, wildcard_paths={wildcard_count}, '
            f'multi_path_operations={paths_count}, template_references={variable_count}, replay_verified=true.'
        )
        config = {'version': 1, 'defaults_profile': 'concise-v1', 'variables': variables or {}, 'options': {'atomic_write': True}, 'operations': ops}
        result = YamlPatchEngine().apply_document(clone(before), config, track_no_effect=False)
        verified = strict_yaml_equal(result, after)
        strategy = 'semantic' if verified else 'fallback-replace'
        if not verified:
            self.warnings.append('Semantic operations did not reproduce the target; replaced the full document.')
            config['operations'] = [{'id': 'replace-document', 'op': 'replace', 'path': '$', 'value': deepcopy(after)}]
            result = YamlPatchEngine().apply_document(clone(before), config, track_no_effect=False)
        self._optimization_session_active = False
        return CompileResult(config, strict_yaml_equal(result, after), strategy, list(self.warnings))


    @classmethod
    def _semantic_fingerprint(cls, value: Any) -> Any:
        """Build an exact, hashable fingerprint without changing mapping order.

        Mapping order and scalar type are intentionally retained because strict YAML
        replay treats both as meaningful. This makes replay caching an execution-only
        optimization: two candidates share a result only when their complete semantic
        input is identical.
        """
        if isinstance(value, dict):
            return ('dict', tuple((cls._semantic_fingerprint(k), cls._semantic_fingerprint(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return ('list', tuple(cls._semantic_fingerprint(v) for v in value))
        return ('scalar', type(value).__module__, type(value).__qualname__, repr(value))

    def _cached_replay(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> bool:
        key = self._semantic_fingerprint(operations)
        cached = self._replay_cache.get(key)
        if cached is not None:
            self._replay_cache_hits += 1
            return cached
        try:
            config = {'version': 1, 'defaults_profile': 'concise-v1', 'options': {'atomic_write': True}, 'operations': operations}
            verified = strict_yaml_equal(YamlPatchEngine().apply_document(clone(before), config, track_no_effect=False), after)
        except Exception:
            verified = False
        self._replay_cache[key] = verified
        return verified

    def _candidate_replays(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> bool:
        return self._cached_replay(before, after, operations)

    @staticmethod
    def _quality_metrics(operations: list[dict[str, Any]]) -> tuple[int, int, int, int, int]:
        """Lower is better. Prefer fewer operations/lines, less nesting and repetition.

        Wildcards are rewarded because they express a complete current sibling/list
        behavior compactly. Exact unions remain useful for partial sibling sets but
        are intentionally less preferred than ``*``/``[*]``.
        """
        op_count = len(operations)
        text = repr(operations)
        size = len(text)
        nesting = 0
        control_noise = 0
        selector_cost = 0

        def walk(value: Any, depth: int = 0) -> None:
            nonlocal nesting, control_noise, selector_cost
            if isinstance(value, dict):
                nesting += max(0, depth - 2)
                for key, child in value.items():
                    if key in {'expect_matches', 'on_multiple_matches', 'missing', 'duplicate', 'item_operations'}:
                        control_noise += 1
                    if key == 'path' and isinstance(child, str):
                        if '[*]' in child or '/*' in child:
                            selector_cost -= 4
                        if re.search(r'/\[[^*][^]]*,[^]]+\]', child):
                            selector_cost += 2
                    if key == 'paths' and isinstance(child, list):
                        # Explicit multi-path operations are clearer than repeated
                        # operations but intentionally rank below one wildcard.
                        selector_cost += max(0, len(child) - 1)
                        control_noise += 1
                    walk(child, depth + 1)
            elif isinstance(value, list):
                nesting += max(0, depth - 2)
                for child in value:
                    walk(child, depth + 1)
        walk(operations)
        return (op_count, control_noise, nesting, size, selector_cost)

    def _is_better(self, candidate: list[dict[str, Any]], current: list[dict[str, Any]]) -> bool:
        if candidate == current:
            return False
        c = self._quality_metrics(candidate)
        p = self._quality_metrics(current)
        # Operation reduction is strongest. With equal operations, require less
        # control noise/nesting/serialized size or a clearer wildcard selector.
        return c < p

    def _make_readable(self, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw in operations:
            op = deepcopy(raw)
            op.pop('missing', None) if op.get('missing') == 'skip' else None
            if op.get('expect_matches') == 1:
                op.pop('expect_matches', None)
            if op.get('on_multiple_matches') == 'error':
                op.pop('on_multiple_matches', None)
            elif op.get('on_multiple_matches') == 'all':
                name = op.get('op')
                match = op.get('match')
                simple_unique_match = (
                    name in {'update_item', 'upsert_item'}
                    and isinstance(match, dict)
                    and 'any' not in match
                    and int(op.get('expect_matches', 1)) == 1
                )
                if name not in {'update_item','upsert_item','remove_item','move_item','copy_item'} or simple_unique_match:
                    # Direct selector paths are expanded into concrete paths. For a
                    # simple identity match, each concrete list still expects one item,
                    # so the explicit all-matches flag is redundant.
                    op.pop('on_multiple_matches', None)
            source = op.get('source')
            if isinstance(source, dict) and 'match' in source:
                match = deepcopy(source['match'])
                op.pop('source', None)
                op['from'] = match
            pos = op.get('position')
            if isinstance(pos, dict):
                if 'before' in pos and isinstance(pos['before'], dict) and 'match' in pos['before']:
                    op.pop('position', None); op['before'] = deepcopy(pos['before']['match'])
                elif 'after' in pos and isinstance(pos['after'], dict) and 'match' in pos['after']:
                    op.pop('position', None); op['after'] = deepcopy(pos['after']['match'])
                elif any(k in pos for k in ('after_key','before_key','first','last','index')):
                    op.pop('position', None); op['place'] = deepcopy(pos)
            nested = op.get('item_operations')
            if isinstance(nested, list):
                set_values = deepcopy(op.get('set', {}))
                merge_values: dict[str, Any] = {}
                remaining=[]
                for n in nested:
                    path=str(n.get('path',''))
                    if n.get('op') == 'replace' and path.startswith('$/') and '/' not in path[2:]:
                        set_values[path[2:]] = deepcopy(n.get('value'))
                    elif n.get('op') == 'insert_key' and path.startswith('$/'):
                        parts=[x for x in path[2:].split('/') if x]
                        cur=merge_values
                        for part in parts:
                            cur=cur.setdefault(part,{})
                        cur[n['key']]=deepcopy(n.get('value'))
                    else:
                        remaining.append(n)
                if set_values: op['set']=set_values
                if merge_values: op['merge']=merge_values
                if remaining: op['item_operations']=remaining
                else: op.pop('item_operations',None)
            result.append(omit_concise_defaults(op))
        return result

    def _diff(self, a: Any, b: Any, path: str, ops: list[dict[str, Any]]) -> None:
        if type(a) is not type(b):
            ops.append(self._replace_scalar_op(path, b, a)); return
        if isinstance(a, dict):
            self._diff_mapping(a,b,path,ops)
        elif isinstance(a, list):
            self._diff_list(a,b,path,ops)
        elif a != b:
            replacement = self._string_replacement(a, b)
            if replacement is not None:
                ops.append({'op':'replace_value','path':path,**replacement})
            else:
                ops.append(self._replace_scalar_op(path, b, a))

    @staticmethod
    def _explicit_mapping_keys(value: dict[str, Any]) -> list[Any]:
        """Return keys physically declared in a YAML mapping.

        ruamel exposes keys inherited through ``<<: *anchor`` as normal mapping
        keys. Diffing those inherited keys generates redundant operations on each
        alias consumer and materializes the merged values when dumped, destroying
        the anchor/alias representation. Only explicit keys belong to the consumer;
        changes to inherited values must be emitted against the anchor source.
        """
        non_merged_items = getattr(value, 'non_merged_items', None)
        if callable(non_merged_items):
            return [key for key, _ in non_merged_items()]
        return list(value)

    def _diff_mapping(self, a: dict[str,Any], b: dict[str,Any], path: str, ops: list[dict[str,Any]]) -> None:
        akeys, bkeys = self._explicit_mapping_keys(a), self._explicit_mapping_keys(b)
        removed = [k for k in akeys if k not in b]
        added = [k for k in bkeys if k not in a]
        # conservative rename inference: pair removed/added keys only when the value match is unique
        renamed: list[tuple[str, str]] = []
        for old_key in list(removed):
            candidates = [new_key for new_key in added if a[old_key] == b[new_key]]
            if len(candidates) == 1:
                new_key = candidates[0]
                renamed.append((old_key, new_key))
                removed.remove(old_key); added.remove(new_key)
        if renamed:
            a = deepcopy(a)
            for old_key, new_key in renamed:
                ops.append({'op':'rename_key','path':path,'old_key':old_key,'new_key':new_key,'missing':'skip'})
                value = a.pop(old_key)
                idx = bkeys.index(new_key)
                if hasattr(a, 'insert'): a.insert(min(idx, len(a)), new_key, value)
                else: a[new_key] = value
        for k in removed:
            ops.append({'op':'remove','path':self._join(path,k),'missing':'skip'})
        for k in added:
            pos: dict[str,Any] = {}
            idx = bkeys.index(k)
            prior = next((x for x in reversed(bkeys[:idx]) if x in a or x in added[:added.index(k)]), None)
            following = next((x for x in bkeys[idx+1:] if x in a), None)
            if prior is not None: pos = {'after_key': prior}
            elif following is not None: pos = {'before_key': following}
            elif idx == 0: pos = {'first': True}
            else: pos = {'last': True}
            reused = self._find_reusable_node(a, b[k], path)
            if reused is not None:
                kind, source_path, source_match = reused
                if kind == 'node':
                    ops.append({'op':'copy_node','from_path':source_path,'to_path':self._join(path,k),'position':pos})
                else:
                    ops.append({'op':'copy_item_to_node','from_path':source_path,'source':{'match':source_match,'expect_matches':1},'to_path':self._join(path,k),'position':pos})
            else:
                ops.append({'op':'insert_key','path':path,'key':k,'value':deepcopy(b[k]),'position':pos})
        for k in [x for x in akeys if x in b]:
            self._diff(a[k], b[k], self._join(path,k), ops)
        # Enforce mapping order only when rename/remove/insert operations do not
        # already yield the requested order. Avoiding no-op key moves is critical
        # for round-trip comments attached to mapping keys.
        current_order = list(akeys)
        for old_key, new_key in renamed:
            if old_key in current_order:
                current_order[current_order.index(old_key)] = new_key
        current_order = [key for key in current_order if key not in removed]
        for key in added:
            target_index = bkeys.index(key)
            current_order.insert(min(target_index, len(current_order)), key)
        if current_order != bkeys:
            for idx, key in enumerate(bkeys):
                if idx < len(current_order) and current_order[idx] == key:
                    continue
                if key in current_order:
                    current_order.remove(key)
                current_order.insert(idx, key)
                if idx > 0:
                    position = {'after_key': bkeys[idx - 1]}
                elif len(bkeys) > 1:
                    position = {'before_key': bkeys[1]}
                else:
                    position = {'first': True}
                ops.append({'op':'move_key','path':path,'source_key':key,'target_key':key,'position':position,'on_conflict':'replace'})

    def _diff_list(self, a: list[Any], b: list[Any], path: str, ops: list[dict[str,Any]]) -> None:
        if strict_equal(a, b): return
        keys = self._identity_keys(path,a,b)
        if not keys or not all(isinstance(x,dict) for x in a+b):
            ops.append({'op':'replace','path':path,'value':deepcopy(b)}); return
        aid = {self._identity(x,keys): x for x in a}; bid = {self._identity(x,keys): x for x in b}
        if len(aid) != len(a) or len(bid) != len(b):
            self.warnings.append(f'{path}: identity keys {keys} were not unique; replaced array.')
            ops.append({'op':'replace','path':path,'value':deepcopy(b)}); return
        for pos,item in enumerate(b):
            ident=self._identity(item,keys)
            if ident not in aid:
                position=self._relative_list_position(b, pos, keys)
                clone = self._find_clone(aid,item,keys)
                if clone is not None:
                    clone_id, set_values, item_operations = clone
                    copy_op={'op':'copy_item','path':path,'source':{'match':dict(zip(keys,clone_id)),'expect_matches':1},'set':set_values,'position':position}
                    if self.retry_protection:
                        copy_op['duplicate']={'unique_by':keys,'policy':'skip'}
                    if item_operations:
                        copy_op['item_operations']=item_operations
                    ops.append(copy_op)
                else:
                    insert_op={'op':'insert','path':path,'position':position,'value':deepcopy(item)}
                    if self.retry_protection:
                        insert_op['duplicate']={'unique_by':keys,'policy':'skip'}
                    ops.append(insert_op)
        for ident in aid.keys()-bid.keys():
            ops.append({'op':'remove_item','path':path,'match':dict(zip(keys,ident)),
                        'missing':'skip','on_multiple_matches':'error'})
        for ident in aid.keys() & bid.keys():
            old,new=aid[ident],bid[ident]
            # Diff inside the existing item instead of replacing changed
            # top-level containers wholesale. Replacing a routes/config list
            # would discard comments and formatting on every unchanged child.
            item_operations: list[dict[str, Any]] = []
            self._diff(old, new, '$', item_operations)
            if item_operations:
                ops.append({
                    'op':'update_item',
                    'path':path,
                    'match':dict(zip(keys,ident)),
                    'item_operations':item_operations,
                    'expect_matches':1,
                })
        # generate deterministic moves after add/remove/update
        current=[self._identity(x,keys) for x in a if self._identity(x,keys) in bid]
        for pos,item in enumerate(b):
            ident=self._identity(item,keys)
            if ident not in current: current.insert(min(pos,len(current)),ident)
        target=[self._identity(x,keys) for x in b]
        for idx,ident in enumerate(target):
            if idx < len(current) and current[idx] == ident: continue
            position=self._relative_identity_position(target, idx, keys)
            if ident in current: current.remove(ident)
            current.insert(idx,ident)
            ops.append({'op':'move_item','path':path,'match':dict(zip(keys,ident)),'position':position,'expect_matches':1})


    @staticmethod
    def _relative_list_position(items:list[dict[str,Any]], index:int, keys:list[str]) -> dict[str,Any]:
        """Return a content-based position; generated list operations never depend on index."""
        if index > 0:
            previous = items[index - 1]
            return {'after': {'match': {key: previous[key] for key in keys}, 'expect_matches': 1}}
        if index + 1 < len(items):
            following = items[index + 1]
            return {'before': {'match': {key: following[key] for key in keys}, 'expect_matches': 1}}
        return {'last': True}

    @staticmethod
    def _relative_identity_position(identities:list[tuple[Any,...]], index:int, keys:list[str]) -> dict[str,Any]:
        """Position a moved item relative to another stable identity instead of an array index."""
        if index > 0:
            return {'after': {'match': dict(zip(keys, identities[index - 1])), 'expect_matches': 1}}
        if index + 1 < len(identities):
            return {'before': {'match': dict(zip(keys, identities[index + 1])), 'expect_matches': 1}}
        return {'last': True}

    def _identity_keys(self,path:str,a:list[Any],b:list[Any]) -> list[str] | None:
        configured=self.identity_rules.get(path)
        if configured is None and path.startswith('$/'):
            configured = self.identity_rules.get('$.' + path[2:].replace('/', '.'))
        if configured is None and path.startswith('$.'):
            configured = self.identity_rules.get('$/' + path[2:].replace('.', '/'))
        if configured and self._valid_identity(configured,a,b): return configured
        all_items=[x for x in a+b if isinstance(x,dict)]
        for key in self.ID_KEYS:
            if self._valid_identity([key],a,b): return [key]
        # infer a small composite key
        common=set.intersection(*(set(x.keys()) for x in all_items)) if all_items else set()
        candidates=[k for k in self.ID_KEYS if k in common]
        for i,k1 in enumerate(candidates):
            for k2 in candidates[i+1:]:
                if self._valid_identity([k1,k2],a,b): return [k1,k2]
        return None

    @staticmethod
    def _valid_identity(keys:list[str],a:list[Any],b:list[Any]) -> bool:
        if not all(isinstance(x,dict) and all(k in x for k in keys) for x in a+b): return False
        try:
            av=[tuple(x[k] for k in keys) for x in a]; bv=[tuple(x[k] for k in keys) for x in b]
            return len(av)==len(set(av)) and len(bv)==len(set(bv))
        except TypeError:
            return False

    @staticmethod
    def _identity(item:dict[str,Any],keys:list[str]) -> tuple[Any,...]:
        values = tuple(item[k] for k in keys)
        hash(values)
        return values

    def _find_clone(self, amap:dict[tuple[Any,...],dict[str,Any]], item:dict[str,Any], keys:list[str]) -> tuple[tuple[Any,...],dict[str,Any],list[dict[str,Any]]] | None:
        """Find a unique, safely reusable list item using a recursive diff.

        The previous implementation only cloned nearly identical top-level
        mappings. Large version/service sections with nested config/list edits
        therefore fell back to embedding the entire new item in patch.yaml.
        Besides producing huge patches, serialising that embedded value could
        shift comment columns. A recursive item diff keeps the source item's
        comments/anchors/style and applies only the real changes.
        """
        candidates: list[tuple[int, int, tuple[Any, ...], list[dict[str, Any]]]] = []
        saved_warnings = list(self.warnings)
        for ident, old in amap.items():
            nested_ops: list[dict[str, Any]] = []
            try:
                self._diff(old, item, '$', nested_ops)
            except Exception:
                continue
            finally:
                # Candidate exploration must not leak warnings from rejected
                # sources into the final compiler report.
                self.warnings = list(saved_warnings)
            if not nested_ops:
                continue
            cost = self._operation_cost(nested_ops)
            full_cost = max(1, self._node_size(item))
            # Reject candidates whose transformation is no smaller/safer than
            # inserting the full item. Root-container replacement is heavily
            # weighted by _operation_cost and naturally fails this check.
            if cost >= full_cost:
                continue
            distance = self._version_like_distance(old, item, None, None, keys)
            candidates.append((cost, distance, ident, nested_ops))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], x[1]))
        if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
            return None
        _, _, ident, nested_ops = candidates[0]
        # Keep identity overrides in the established compact `set` field. This
        # also ensures duplicate detection sees the new identity before insert.
        set_values: dict[str, Any] = {}
        remaining: list[dict[str, Any]] = []
        identity_paths = {f'$/'+str(key).replace('~','~0').replace('/','~1'): key for key in keys}
        for operation in nested_ops:
            key = identity_paths.get(str(operation.get('path')))
            if operation.get('op') == 'replace' and key is not None:
                set_values[key] = deepcopy(operation.get('value'))
            elif operation.get('op') == 'replace_value' and key is not None:
                set_values[key] = deepcopy(item[key])
            else:
                nested_operation = deepcopy(operation)
                # The copy is always built from the original source item before
                # duplicate verification, so nested renames do not require the
                # top-level compiler's idempotent missing:skip marker.
                if nested_operation.get('op') == 'rename_key':
                    nested_operation.pop('missing', None)
                remaining.append(nested_operation)
        return ident, set_values, remaining

    @staticmethod
    def _node_size(value: Any) -> int:
        if isinstance(value, dict):
            return 1 + sum(DiffCompiler._node_size(k) + DiffCompiler._node_size(v) for k, v in value.items())
        if isinstance(value, list):
            return 1 + sum(DiffCompiler._node_size(v) for v in value)
        return 1

    @staticmethod
    def _operation_cost(operations: list[dict[str, Any]]) -> int:
        cost = 0
        for op in operations:
            name = op.get('op')
            value = op.get('value')
            if name == 'replace' and isinstance(value, (dict, list)):
                cost += DiffCompiler._node_size(value) + 20
            elif name in {'insert', 'insert_key'}:
                cost += 2 + DiffCompiler._node_size(value)
            else:
                cost += 1
            nested = op.get('item_operations')
            if isinstance(nested, list):
                cost += DiffCompiler._operation_cost(nested)
        return cost

    @staticmethod
    def _version_like_distance(old: dict[str,Any], new: dict[str,Any], old_key: str | None, new_key: str | None, identity_keys: list[str]) -> int:
        """Score version-like textual changes; lower means a safer clone source."""
        def nums(value: Any) -> list[int]:
            return [int(x) for x in re.findall(r"\d+", str(value))]
        distances: list[int] = []
        for key in identity_keys:
            a, b = nums(old.get(key)), nums(new.get(key))
            if a and b:
                distances.append(sum(abs(x-y) for x,y in zip(a,b)) + 100*abs(len(a)-len(b)))
        if old_key and new_key:
            a, b = nums(old_key), nums(new_key)
            if a and b:
                distances.append(sum(abs(x-y) for x,y in zip(a,b)) + 100*abs(len(a)-len(b)))
        return sum(distances) if distances else 10**9


    def _find_reusable_node(self, root: dict[str, Any], target: Any, path: str) -> tuple[str, str, dict[str, Any] | None] | None:
        """Find an exact existing subtree/list item so generated config stays short."""
        for key, value in root.items():
            source_path = self._join(path, key)
            if value == target:
                return ('node', source_path, None)
            if isinstance(value, list) and isinstance(target, dict):
                keys = self._identity_keys(source_path, value, value)
                if keys:
                    for item in value:
                        if item == target:
                            return ('item', source_path, {k: item[k] for k in keys})
        return None


    @staticmethod
    def _is_numeric_like_scalar(value: str) -> bool:
        """True for complete numeric/version-like scalar text such as 30, -1.5, or 2026.05.

        These values are clearer and safer as a full replace value than as a substring
        replacement (for example search ``4`` -> ``5`` inside ``2026.04``).
        """
        return re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)", value.strip()) is not None

    @staticmethod
    def _string_replacement(old: Any, new: Any) -> dict[str, Any] | None:
        """Return a readable literal substring replacement when it is exact and unambiguous."""
        if not isinstance(old, str) or not isinstance(new, str) or old == new:
            return None
        if DiffCompiler._is_numeric_like_scalar(old) or DiffCompiler._is_numeric_like_scalar(new):
            return None
        prefix = 0
        limit = min(len(old), len(new))
        while prefix < limit and old[prefix] == new[prefix]:
            prefix += 1
        suffix = 0
        while (suffix < len(old) - prefix and suffix < len(new) - prefix and
               old[len(old)-1-suffix] == new[len(new)-1-suffix]):
            suffix += 1
        old_mid = old[prefix:len(old)-suffix if suffix else len(old)]
        new_mid = new[prefix:len(new)-suffix if suffix else len(new)]
        if not old_mid or old.count(old_mid) != 1:
            return None
        if old.replace(old_mid, new_mid, 1) != new:
            return None
        if len(old_mid) + len(new_mid) > len(new) + 12:
            return None
        return {'search': old_mid, 'replacement': new_mid, 'count': 1, 'expect_replacements': 1}

    def _optimize_selectors(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Safely compact generated operations.

        The optimizer performs three verified passes:
        1. common mapping additions -> wildcard ``set ... missing:create``;
        2. common portions of list-item updates -> wildcard item paths;
        3. otherwise identical scalar operations -> wildcard paths.

        Every pass is accepted only when replaying the complete candidate config
        reproduces ``after`` with strict key/list ordering and scalar types.
        """
        current = deepcopy(operations)
        # One compile owns one deadline/candidate budget.  Post-readable and outer
        # passes continue from the last replay-verified state instead of resetting
        # the clock and reconsidering the original operation list.
        if not self._optimization_session_active:
            self._optimization_deadline = time.monotonic() + max(0.05, self.optimization_timeout_seconds)
            self._optimization_candidates = 0
            self._optimization_limit_reported = False
            self._large_mode_replays = 0
            self._optimization_session_active = True
        if len(current) > self.large_mode_threshold:
            return self._optimize_large_mode(before, after, current)
        seen_states: set[str] = set()
        # Run the optimizer to a verified fixed point. A successful pass can expose
        # new candidates for the next pass (for example exact paths -> wildcard
        # paths -> one parent-level merge). The round limit prevents pathological
        # configs from spending unbounded time in optimization.
        max_rounds = 6
        for round_no in range(1, max_rounds + 1):
            if self._optimization_budget_exhausted():
                break
            before_round = repr(current)
            if before_round in seen_states:
                self.warnings.append(f'Config optimization stopped because a repeated state was detected at round {round_no}.')
                break
            seen_states.add(before_round)
            passes = []
            if len(current) <= 500:
                passes.extend([self._optimize_collective_all_item_updates, self._optimize_all_item_updates, self._deduplicate_operations, self._optimize_common_insert_keys, self._optimize_common_item_operations])
            elif round_no == 1:
                self.warnings.append(
                    f'Skipped high-cost partial common-difference extraction for {len(current)} operations; linear verified passes remain enabled.'
                )
            passes.extend([self._optimize_same_target_updates, self._optimize_dependency_aware_update_merges, self._optimize_identical_paths, self._optimize_parent_merges, self._remove_redundant_paths])
            for optimizer in passes:
                if self._optimization_budget_exhausted():
                    break
                checkpoint = deepcopy(current)
                try:
                    candidate = optimizer(before, after, current)
                    if candidate == checkpoint:
                        current = checkpoint
                    else:
                        verified_candidate = self._verified_operations(before, after, candidate)
                        # Common update_item extraction is a structural normalization:
                        # shared nested changes belong in one wildcard/match-union
                        # operation while per-item residuals remain separate. It may
                        # add one operation for small groups, but removes duplicated
                        # behavior and scales much better as item count grows.
                        structural_common_item = optimizer.__name__ == '_optimize_common_item_operations'
                        if verified_candidate and (self._is_better(candidate, checkpoint) or structural_common_item):
                            current = candidate
                        else:
                            current = checkpoint
                            self.warnings.append(f'Optimizer {optimizer.__name__} candidate failed replay or did not improve readability; rolled back.')
                except Exception as exc:
                    current = checkpoint
                    self.warnings.append(f'Optimizer {optimizer.__name__} failed with {type(exc).__name__}; rolled back safely.')
            if repr(current) == before_round:
                if round_no > 1:
                    self.warnings.append(f'Config optimization converged after {round_no} rounds.')
                break
        else:
            self.warnings.append(f'Config optimization stopped at the safety limit of {max_rounds} rounds.')
        return current


    @staticmethod
    def _apply_large_proposals(operations: list[dict[str, Any]], proposals: list[tuple[set[int], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
        """Apply non-overlapping indexed replacements while preserving source order."""
        by_first = {min(indices): (indices, replacements) for indices, replacements in proposals}
        removed = set().union(*(indices for indices, _ in proposals)) if proposals else set()
        result: list[dict[str, Any]] = []
        for index, operation in enumerate(operations):
            proposal = by_first.get(index)
            if proposal is not None:
                result.extend(deepcopy(proposal[1]))
            if index not in removed:
                result.append(deepcopy(operation))
        return result

    def _accept_large_proposals(self, before: Any, after: Any, current: list[dict[str, Any]], proposals: list[tuple[set[int], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
        """Accept proposal batches with bounded replay and checkpoint each success.

        A failed batch is split, so one unsafe simplification does not discard safe
        inner/outer work.  ``current`` is always the last replay-verified state.
        """
        pending = [p for p in proposals if p[0]]
        while pending and not self._optimization_budget_exhausted() and self._large_mode_replays < self.large_mode_max_replays:
            trial = self._apply_large_proposals(current, pending)
            self._large_mode_replays += 1
            if self._verified_operations(before, after, trial):
                current = trial
                break
            if len(pending) == 1:
                break
            midpoint = len(pending) // 2
            # Process the first half against the latest verified checkpoint, then
            # rebuild indexes before later phases.  This avoids stale original data.
            first, second = pending[:midpoint], pending[midpoint:]
            first_trial = self._apply_large_proposals(current, first)
            self._large_mode_replays += 1
            if self._verified_operations(before, after, first_trial):
                current = first_trial
                # Remaining proposal indexes refer to the previous checkpoint.
                # Stop this phase; the caller rebuilds fresh groups from current.
                break
            if self._large_mode_replays >= self.large_mode_max_replays or self._optimization_budget_exhausted():
                break
            pending = second
        return current

    def _large_inner_proposals(self, operations: list[dict[str, Any]]) -> list[tuple[set[int], list[dict[str, Any]]]]:
        """Build collective all-item lowering proposals without recursive scans."""
        by_path: dict[str, list[int]] = {}
        for index, operation in enumerate(operations):
            if operation.get('op') == 'update_item' and isinstance(operation.get('path'), str):
                by_path.setdefault(str(operation['path']), []).append(index)
        proposals: list[tuple[set[int], list[dict[str, Any]]]] = []
        for _, indices in by_path.items():
            if len(indices) < 2:
                continue
            direct: list[dict[str, Any]] = []
            valid = True
            for index in indices:
                lowered = self._direct_update_item_operations(operations[index])
                if not lowered:
                    valid = False
                    break
                direct.extend(lowered)
            if not valid:
                continue
            unique: list[dict[str, Any]] = []
            seen: set[str] = set()
            for operation in direct:
                signature = json.dumps(self._semantic_normalize(operation), ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
                if signature not in seen:
                    seen.add(signature)
                    unique.append(operation)
            proposals.append((set(indices), unique))
        return proposals

    def _large_outer_proposals(self, operations: list[dict[str, Any]]) -> list[tuple[set[int], list[dict[str, Any]]]]:
        """Build one wildcard/paths proposal per semantic operation group."""
        groups: dict[str, list[int]] = {}
        supported = {'replace','set','replace_value','remove','insert_key','copy_item','remove_item','update_item','move_item','merge','append','prepend','upsert_item'}
        for index, operation in enumerate(operations):
            if operation.get('op') not in supported or not isinstance(operation.get('path'), str):
                continue
            groups.setdefault(self._normalized_operation_signature(operation), []).append(index)
        proposals: list[tuple[set[int], list[dict[str, Any]]]] = []
        for indices in sorted(groups.values(), key=lambda xs: (-len(xs), min(xs))):
            if len(indices) < 2:
                continue
            paths = [str(operations[index]['path']) for index in indices]
            merged = deepcopy(operations[indices[0]])
            wildcard = self._wildcard_path(paths)
            if wildcard is not None:
                merged['path'] = wildcard
                merged.pop('paths', None)
                merged['on_multiple_matches'] = 'all'
            else:
                merged.pop('path', None)
                merged['paths'] = list(dict.fromkeys(paths))
            proposals.append((set(indices), [merged]))
        return proposals

    def _optimize_large_mode(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bounded >500-operation optimizer: inner checkpoint, then outer batch.

        No recursive restart is used.  Every accepted phase becomes the new
        replay-verified baseline, so the outer pass never reads the unoptimized
        original operations.  Correctness remains guarded by final strict replay.
        """
        started = time.monotonic()
        current = deepcopy(operations)
        self.warnings.append(
            f'Large optimization mode enabled for {len(current)} operations; '
            'using inner-first verified checkpoints and bounded batch replay.'
        )
        # Cheap exact semantic de-duplication first; verify as one batch.
        deduped = self._deduplicate_operations(before, after, current)
        if deduped != current and self._verified_operations(before, after, deduped):
            current = deduped
        # Phase 1: inner update_item lowering.  Success is immediately checkpointed.
        inner = self._large_inner_proposals(current)
        if inner and not self._optimization_budget_exhausted():
            current = self._accept_large_proposals(before, after, current, inner)
        # Phase 2: rebuild groups from the optimized checkpoint, then collapse outer paths.
        outer = self._large_outer_proposals(current)
        if outer and not self._optimization_budget_exhausted():
            current = self._accept_large_proposals(before, after, current, outer)
        elapsed = time.monotonic() - started
        self.warnings.append(
            f'Large optimization mode finished in {elapsed:.3f}s with '
            f'{self._large_mode_replays} batch replay attempt(s); kept {len(current)} operations.'
        )
        return current

    def _optimization_budget_exhausted(self) -> bool:
        timed_out = time.monotonic() >= self._optimization_deadline if self._optimization_deadline else False
        too_many = self._optimization_candidates >= self.optimization_max_candidates
        if (timed_out or too_many) and not self._optimization_limit_reported:
            reason = 'time budget' if timed_out else 'candidate limit'
            self.warnings.append(
                f'Config optimization stopped at the {reason}; kept the last replay-verified config '
                f'(candidates={self._optimization_candidates}, limit={self.optimization_max_candidates}, timeout={self.optimization_timeout_seconds:g}s).'
            )
            self._optimization_limit_reported = True
        return timed_out or too_many

    def _verified_operations(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> bool:
        if self._optimization_budget_exhausted():
            return False
        key = self._semantic_fingerprint(operations)
        if key in self._replay_cache:
            self._replay_cache_hits += 1
            return self._replay_cache[key]
        self._optimization_candidates += 1
        return self._cached_replay(before, after, operations)

    @staticmethod
    def _wildcard_path(paths: list[str]) -> str | None:
        if len(paths) < 2:
            return None
        token_sets = [parse_path(path) for path in paths]
        length = len(token_sets[0])
        if length == 0 or any(len(tokens) != length for tokens in token_sets):
            return None
        varying = [pos for pos in range(length) if len({tokens[pos] for tokens in token_sets}) > 1]
        if not varying:
            return None
        candidate = list(token_sets[0])
        for pos in varying:
            candidate[pos] = '*'
        raw_parts = [path[2:].split('/') if path.startswith('$/') else path.lstrip('/').split('/') for path in paths]
        encoded: list[str] = []
        for pos, token in enumerate(candidate):
            if token == '*' and pos not in varying and all(parts[pos] == '[*]' for parts in raw_parts):
                encoded.append('[*]')
            else:
                encoded.append(str(token).replace('~', '~0').replace('/', '~1'))
        return '$/' + '/'.join(encoded)

    @staticmethod
    def _union_path(paths: list[str]) -> str | None:
        """Build an exact key-union selector such as $/root/[a,b,c]."""
        if len(paths) < 2:
            return None
        token_sets = [parse_path(path) for path in paths]
        length = len(token_sets[0])
        if length == 0 or any(len(tokens) != length for tokens in token_sets):
            return None
        varying = [pos for pos in range(length) if len({tokens[pos] for tokens in token_sets}) > 1]
        if len(varying) != 1:
            return None
        pos = varying[0]
        if not all(isinstance(tokens[pos], str) and tokens[pos] != '*' for tokens in token_sets):
            return None
        candidate = list(token_sets[0])
        candidate[pos] = tuple(str(tokens[pos]) for tokens in token_sets)
        encoded = []
        for token in candidate:
            if isinstance(token, tuple):
                encoded.append('[' + ','.join(token) + ']')
            else:
                encoded.append(str(token).replace('~', '~0').replace('/', '~1'))
        return '$/' + '/'.join(encoded)

    def _selector_candidates(self, paths: list[str]) -> list[str]:
        """Return selector candidates in simplification priority order.

        Wildcards are intentionally attempted first. Safety is decided by full
        document replay, not by requiring the selector to match exactly the
        original concrete path set. This preserves useful cases such as
        ``replace_value`` where extra matched nodes are unaffected by the search.
        Exact unions are the fallback for truly over-broad wildcards.
        """
        candidates: list[str] = []
        wildcard = self._wildcard_path(paths)
        if wildcard is not None:
            candidates.append(wildcard)
        union = self._union_path(paths)
        if union is not None and union not in candidates:
            candidates.append(union)
        return candidates

    def _optimize_common_insert_keys(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract identical added keys/sections under sibling mappings.

        Wildcard is attempted before exact union and each candidate is accepted
        independently by strict replay. A failing group no longer prevents other
        valid groups from being simplified.
        """
        original = deepcopy(operations)
        groups: dict[str, list[int]] = {}
        for index, operation in enumerate(original):
            if operation.get('op') != 'insert_key':
                continue
            path, key = operation.get('path'), operation.get('key')
            if not isinstance(path, str) or not isinstance(key, str) or '*' in path:
                continue
            signature = {'key': key, 'value': deepcopy(operation.get('value'))}
            groups.setdefault(repr(signature), []).append(index)

        for indices in groups.values():
            if len(indices) < 2:
                continue
            parent_paths = [str(original[index]['path']) for index in indices]
            key = str(original[indices[0]]['key'])
            for selector_parent in self._selector_candidates(parent_paths):
                candidate = {
                    'op': 'set',
                    'path': self._join(selector_parent, key),
                    'value': deepcopy(original[indices[0]].get('value')),
                    'missing': 'create',
                    'on_multiple_matches': 'all',
                }
                removed = set(indices)
                trial = [deepcopy(op) for i, op in enumerate(original) if i not in removed]
                trial.insert(min(indices), candidate)
                if not self._verified_operations(before, after, trial):
                    continue
                kind = 'wildcard' if '*' in selector_parent else 'exact union'
                self.warnings.append(
                    f'Extracted common added section {key!r} from {len(indices)} sibling mappings into {kind} path {candidate["path"]}.'
                )
                return self._optimize_common_insert_keys(before, after, trial)
        return original

    @staticmethod
    def _relative_item_operation_signature(operation: dict[str, Any]) -> str:
        normalized = deepcopy(operation)
        normalized.pop('id', None)
        return repr(normalized)

    @staticmethod
    def _direct_item_operation(list_path: str, nested: dict[str, Any]) -> dict[str, Any] | None:
        """Convert an item-relative operation into a direct wildcard operation."""
        name = nested.get('op')
        relative = nested.get('path')
        if not isinstance(relative, str):
            return None
        item_root = list_path.rstrip('/') + '/[*]'
        if name == 'insert_key' and relative == '$' and isinstance(nested.get('key'), str):
            return {
                'op': 'set',
                'path': DiffCompiler._join(item_root, str(nested['key'])),
                'value': deepcopy(nested.get('value')),
                'missing': 'create',
                'on_multiple_matches': 'all',
            }
        if name not in {'replace', 'set', 'replace_value', 'remove'}:
            return None
        if relative == '$':
            direct_path = item_root
        elif relative.startswith('$/'):
            direct_path = item_root + relative[1:]
        else:
            return None
        direct = deepcopy(nested)
        direct['path'] = direct_path
        direct['on_multiple_matches'] = 'all'
        return direct

    def _deduplicate_operations(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove byte-for-byte duplicate generated operations, preserving order."""
        original = deepcopy(operations)
        seen: set[str] = set()
        candidate: list[dict[str, Any]] = []
        removed = 0
        for operation in original:
            normalized = deepcopy(operation)
            normalized.pop('id', None)
            signature = repr(normalized)
            if signature in seen:
                removed += 1
                continue
            seen.add(signature)
            candidate.append(operation)
        if not removed or not self._verified_operations(before, after, candidate):
            return original
        self.warnings.append(f'Removed {removed} duplicate generated operation(s).')
        return candidate

    def _direct_update_item_operations(self, operation: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Lower an update_item that effectively targets every item to direct paths.

        The returned operations intentionally omit ``match`` and express the target
        set through ``[*]``. Full replay decides whether the original selector was
        redundant; when only part of the list matched, the candidate is rejected.
        """
        list_path = operation.get('path')
        if not isinstance(list_path, str):
            return None
        direct: list[dict[str, Any]] = []
        item_root = list_path.rstrip('/') + '/[*]'

        for key, value in (operation.get('set') or {}).items():
            relative = str(key)
            if relative.startswith('$/'):
                path = item_root + relative[1:]
            elif relative.startswith('/'):
                path = item_root + relative
            else:
                path = self._join(item_root, relative)
            direct.append({'op': 'set', 'path': path, 'value': deepcopy(value), 'on_multiple_matches': 'all'})

        for relative in operation.get('remove') or []:
            relative = str(relative)
            if relative == '$':
                path = item_root
            elif relative.startswith('$/'):
                path = item_root + relative[1:]
            elif relative.startswith('/'):
                path = item_root + relative
            else:
                path = self._join(item_root, relative)
            direct.append({'op': 'remove', 'path': path, 'on_multiple_matches': 'all'})

        if isinstance(operation.get('merge'), dict):
            direct.append({
                'op': 'merge', 'path': item_root,
                'value': deepcopy(operation['merge']),
                'merge_strategy': operation.get('merge_strategy', 'overwrite'),
                'on_multiple_matches': 'all',
            })

        for nested in operation.get('item_operations') or []:
            if not isinstance(nested, dict):
                return None
            converted = self._direct_item_operation(list_path, nested)
            if converted is None:
                return None
            direct.append(converted)

        return direct or None

    def _optimize_collective_all_item_updates(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Lower a complete group of update_item selectors to direct ``[*]`` paths.

        A single match may cover only part of a list and therefore cannot be
        removed in isolation.  Several generated update_item operations at the
        same list path can collectively cover every item.  Test the group as one
        atomic candidate before match-union extraction so exhaustive selectors are
        removed instead of being preserved as a more complicated ``match:any``.
        """
        original = deepcopy(operations)
        by_path: dict[str, list[int]] = {}
        for index, operation in enumerate(original):
            if operation.get('op') != 'update_item':
                continue
            path = operation.get('path')
            if isinstance(path, str):
                by_path.setdefault(path, []).append(index)

        for list_path, indices in by_path.items():
            if len(indices) < 2:
                continue
            direct_operations: list[dict[str, Any]] = []
            convertible = True
            for index in indices:
                direct = self._direct_update_item_operations(original[index])
                if not direct:
                    convertible = False
                    break
                direct_operations.extend(direct)
            if not convertible:
                continue

            # Semantic de-duplication is important here because different matches
            # often lower to the same direct operation.
            unique_direct: list[dict[str, Any]] = []
            seen: set[str] = set()
            for operation in direct_operations:
                signature = json.dumps(
                    self._semantic_normalize(operation),
                    ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str,
                )
                if signature in seen:
                    continue
                seen.add(signature)
                unique_direct.append(operation)

            removed = set(indices)
            trial = [deepcopy(operation) for index, operation in enumerate(original) if index not in removed]
            insert_at = min(indices)
            for offset, operation in enumerate(unique_direct):
                trial.insert(min(insert_at + offset, len(trial)), operation)
            if not self._verified_operations(before, after, trial):
                continue
            self.warnings.append(
                f'Removed collectively exhaustive update_item matches at {list_path} and expressed the changes with [*].'
            )
            return self._optimize_collective_all_item_updates(before, after, trial)
        return original

    def _optimize_all_item_updates(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove redundant item matches when every item receives the same change.

        One update_item is tested at a time. A candidate is accepted only when the
        complete generated config still reproduces ``after``. This makes selectors
        such as ``match: {name: A}`` disappear only when they have no filtering value.
        """
        original = deepcopy(operations)
        for index, operation in enumerate(original):
            if operation.get('op') != 'update_item':
                continue
            direct = self._direct_update_item_operations(operation)
            if not direct:
                continue
            trial = [deepcopy(op) for op in original[:index]] + direct + [deepcopy(op) for op in original[index + 1:]]
            if not self._verified_operations(before, after, trial):
                continue
            self.warnings.append(
                f'Removed a redundant update_item match at {operation.get("path")} and expressed the all-item change with [*].'
            )
            return self._optimize_all_item_updates(before, after, trial)
        return original

    def _optimize_common_item_operations(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract common nested changes from multiple ``update_item`` operations.

        Common fields are emitted once using ``list/*/...`` while per-item fields
        remain in their original ``update_item`` operation.
        """
        original = deepcopy(operations)
        by_path: dict[str, list[int]] = {}
        for index, operation in enumerate(original):
            if operation.get('op') != 'update_item' or not isinstance(operation.get('item_operations'), list):
                continue
            path = operation.get('path')
            if isinstance(path, str) and '*' not in path:
                by_path.setdefault(path, []).append(index)

        for list_path, indices in by_path.items():
            if len(indices) < 2:
                continue
            occurrence: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
            for operation_index in indices:
                nested_ops = original[operation_index].get('item_operations') or []
                for nested_index, nested in enumerate(nested_ops):
                    if not isinstance(nested, dict):
                        continue
                    signature = self._relative_item_operation_signature(nested)
                    occurrence.setdefault(signature, []).append((operation_index, nested_index, nested))
            candidates = [items for items in occurrence.values() if len({item[0] for item in items}) >= 2]
            candidates.sort(key=lambda items: (-len({item[0] for item in items}), items[1][1] if len(items) > 1 else 0))
            for items in candidates:
                participating = sorted({item[0] for item in items})
                # Only use a broad wildcard when all existing list items receiving
                # this change are represented; replay verification is the final gate.
                direct = self._direct_item_operation(list_path, items[0][2])
                if direct is None:
                    continue
                trial = deepcopy(original)
                signature = self._relative_item_operation_signature(items[0][2])
                for operation_index in participating:
                    nested_ops = trial[operation_index].get('item_operations') or []
                    trial[operation_index]['item_operations'] = [
                        nested for nested in nested_ops
                        if not (isinstance(nested, dict) and self._relative_item_operation_signature(nested) == signature)
                    ]
                trial = [
                    operation for operation in trial
                    if not (operation.get('op') == 'update_item' and not operation.get('item_operations'))
                ]
                insert_at = min(participating)
                trial.insert(min(insert_at, len(trial)), direct)
                if self._verified_operations(before, after, trial):
                    self.warnings.append(
                        f'Extracted a common list-item change from {len(participating)} items into wildcard path {direct["path"]}.'
                    )
                    return self._optimize_common_item_operations(before, after, trial)

                # Wildcard may be too broad when only a subset of list items shares
                # the nested change. Fall back to one exact update_item using a
                # matcher union instead of keeping N duplicate update_item blocks.
                matches: list[dict[str, Any]] = []
                exact_union_supported = True
                for operation_index in participating:
                    source = original[operation_index]
                    match = source.get('match')
                    if not isinstance(match, dict) or not match:
                        exact_union_supported = False
                        break
                    matches.append(deepcopy(match))
                if not exact_union_supported:
                    continue

                merged_update = deepcopy(original[participating[0]])
                merged_update['match'] = {'any': matches}
                merged_update['item_operations'] = [deepcopy(items[0][2])]
                merged_update['expect_matches'] = len(participating)
                merged_update['on_multiple_matches'] = 'all'

                union_trial = deepcopy(original)
                for operation_index in participating:
                    nested_ops = union_trial[operation_index].get('item_operations') or []
                    union_trial[operation_index]['item_operations'] = [
                        nested for nested in nested_ops
                        if not (isinstance(nested, dict) and self._relative_item_operation_signature(nested) == signature)
                    ]
                union_trial = [
                    operation for operation in union_trial
                    if not (operation.get('op') == 'update_item' and not operation.get('item_operations'))
                ]
                union_trial.insert(min(insert_at, len(union_trial)), merged_update)
                if not self._verified_operations(before, after, union_trial):
                    continue
                self.warnings.append(
                    f'Extracted a common list-item change from {len(participating)} items into one exact match union.'
                )
                return self._optimize_common_item_operations(before, after, union_trial)
        return original

    @classmethod
    def _semantic_normalize(cls, value: Any) -> Any:
        """Return a recursively canonical representation for semantic comparison.

        Generated operations may contain the same fields in a different insertion
        order, or explicitly include default-equivalent options in only one branch.
        Comparing ``repr(dict)`` therefore causes false negatives, especially for
        nested ``update_item.item_operations``.
        """
        if isinstance(value, dict):
            normalized: dict[str, Any] = {}
            for key in sorted(value):
                if key == 'id':
                    continue
                item = value[key]
                if key == 'missing' and item == 'skip':
                    continue
                if key == 'expect_matches' and item == 1:
                    continue
                if key == 'on_multiple_matches' and item == 'error':
                    continue
                normalized[key] = cls._semantic_normalize(item)
            return normalized
        if isinstance(value, list):
            return [cls._semantic_normalize(item) for item in value]
        return value

    def _normalized_operation_signature(self, operation: dict[str, Any]) -> str:
        """Canonical semantic signature excluding only the outer target path.

        ``set`` and ``replace`` are grouped as one scalar-write family during
        generated-config optimization. They are equivalent whenever the concrete
        targets already exist; full replay remains the authority and rejects the
        merged candidate when missing-path behavior would differ. This is required
        after readable lowering, where one sibling may originate from nested
        ``item_operations: replace`` and another from ``set`` shorthand.
        """
        normalized = deepcopy(operation)
        normalized.pop('path', None)
        normalized.pop('paths', None)
        if normalized.get('op') in {'set', 'replace'}:
            normalized['op'] = '__scalar_write__'
        canonical = self._semantic_normalize(normalized)
        return json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)

    @staticmethod
    def _copy_item_source_match(operation: dict[str, Any]) -> Any:
        """Return the copy source selector in a canonical shape."""
        source = operation.get('source')
        if isinstance(source, dict):
            if isinstance(source.get('match'), dict):
                return source.get('match')
            return source
        if isinstance(operation.get('from'), dict):
            return operation.get('from')
        return None

    def _dependency_barrier_is_safe_for_update_merge(
        self,
        operations: list[dict[str, Any]],
        indices: list[int],
    ) -> bool:
        """Allow a very small, proven-safe reorder around per-path copy_item.

        Supported shape::

            copy path-A -> update path-A
            copy path-B -> update path-B

        The updates may be separated only by copy_item operations on one of the
        same target list paths.  The copy source selector must equal the update
        selector, so moving the update after all copies preserves the copied
        source state.  All other operations are a dependency barrier.
        """
        if len(indices) < 2:
            return False
        group_paths = {str(operations[index].get('path')) for index in indices}
        update_match = self._semantic_normalize(operations[indices[0]].get('match'))
        first, last = min(indices), max(indices)
        group_index_set = set(indices)
        crossed_copy = False
        for index in range(first, last + 1):
            if index in group_index_set:
                continue
            operation = operations[index]
            if operation.get('op') != 'copy_item':
                return False
            path = operation.get('path')
            if not isinstance(path, str) or path not in group_paths:
                return False
            source_match = self._copy_item_source_match(operation)
            if self._semantic_normalize(source_match) != update_match:
                return False
            crossed_copy = True
        return crossed_copy

    @staticmethod
    def _replace_group_at_last_index(
        operations: list[dict[str, Any]],
        indices: list[int],
        replacement: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Remove a semantic group and insert its merged form at the last slot."""
        removed = set(indices)
        last = max(indices)
        result: list[dict[str, Any]] = []
        for index, operation in enumerate(operations):
            if index == last:
                result.append(deepcopy(replacement))
            if index not in removed:
                result.append(deepcopy(operation))
        return result

    def _optimize_dependency_aware_update_merges(
        self,
        before: Any,
        after: Any,
        operations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge identical update_item blocks across safe copy-item barriers.

        This is deliberately not a general operation reordering engine.  It only
        handles identical update_item operations whose intervening operations are
        copy_item preparations on the same list paths and from the same match.
        The merged update is placed at the last original update position, after
        every relevant copy has completed.  Full replay remains authoritative.
        """
        original = deepcopy(operations)
        groups: dict[str, list[int]] = {}
        for index, operation in enumerate(original):
            if operation.get('op') != 'update_item' or not isinstance(operation.get('path'), str):
                continue
            groups.setdefault(self._normalized_operation_signature(operation), []).append(index)

        for indices in sorted(groups.values(), key=lambda xs: (-len(xs), min(xs))):
            if len(indices) < 2 or not self._dependency_barrier_is_safe_for_update_merge(original, indices):
                continue
            paths = [str(original[index]['path']) for index in indices]
            base = deepcopy(original[indices[0]])

            candidates: list[tuple[str, dict[str, Any]]] = []
            wildcard = self._wildcard_path(paths)
            if wildcard is not None:
                merged = deepcopy(base)
                merged['path'] = wildcard
                merged.pop('paths', None)
                merged['on_multiple_matches'] = 'all'
                candidates.append((f'wildcard path {wildcard}', merged))

            merged_paths = deepcopy(base)
            merged_paths.pop('path', None)
            merged_paths['paths'] = list(dict.fromkeys(paths))
            candidates.append(('one paths operation', merged_paths))

            union = self._union_path(paths)
            if union is not None:
                merged = deepcopy(base)
                merged['path'] = union
                merged.pop('paths', None)
                merged['on_multiple_matches'] = 'all'
                candidates.append((f'exact union path {union}', merged))

            for description, merged in candidates:
                trial = self._replace_group_at_last_index(original, indices, merged)
                if self._verified_operations(before, after, trial):
                    self.warnings.append(
                        f'Dependency-aware merge moved {len(indices)} identical update_item operations '
                        f'after their copy_item barriers and combined them into {description}.'
                    )
                    return self._optimize_dependency_aware_update_merges(before, after, trial)
        return original

    def _optimize_identical_paths(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge same-semantics direct operations into wildcard/union selectors.

        Groups are processed one at a time. This is important: one unsafe group
        must not roll back every other valid simplification in the file.
        """
        original = deepcopy(operations)
        groups: dict[str, list[int]] = {}
        supported = {
            'replace', 'set', 'replace_value', 'remove', 'insert_key',
            'copy_item', 'remove_item', 'update_item', 'move_item',
            'merge', 'append', 'prepend', 'upsert_item',
        }
        for idx, op in enumerate(original):
            if op.get('op') not in supported:
                continue
            path = op.get('path')
            if not isinstance(path, str) or re.search(r'/\[(?!\*)[^]]+,', path):
                continue
            groups.setdefault(self._normalized_operation_signature(op), []).append(idx)

        # Prefer larger groups, then stable source order.
        ordered_groups = sorted(groups.values(), key=lambda xs: (-len(xs), min(xs)))
        for indices in ordered_groups:
            if len(indices) < 2:
                continue
            paths = [str(original[i]['path']) for i in indices]
            # Layer 1: one wildcard selector.
            wildcard = self._wildcard_path(paths)
            if wildcard is not None:
                merged = deepcopy(original[indices[0]])
                merged['path'] = wildcard
                merged.pop('paths', None)
                merged['on_multiple_matches'] = 'all'
                removed = set(indices)
                trial = [deepcopy(op) for i, op in enumerate(original) if i not in removed]
                trial.insert(min(indices), merged)
                if self._verified_operations(before, after, trial):
                    self.warnings.append(f'Optimized {len(indices)} identical operations into wildcard path {wildcard}.')
                    return self._optimize_identical_paths(before, after, trial)

            # Layer 2: explicit paths. Each entry may itself contain selectors.
            merged = deepcopy(original[indices[0]])
            merged.pop('path', None)
            merged['paths'] = list(dict.fromkeys(paths))
            removed = set(indices)
            trial = [deepcopy(op) for i, op in enumerate(original) if i not in removed]
            trial.insert(min(indices), merged)
            if self._verified_operations(before, after, trial):
                self.warnings.append(f'Optimized {len(indices)} identical operations into one paths operation.')
                return self._optimize_identical_paths(before, after, trial)

            # Layer 3: compact exact union fallback.
            union = self._union_path(paths)
            if union is not None:
                merged = deepcopy(original[indices[0]])
                merged['path'] = union
                merged.pop('paths', None)
                merged['on_multiple_matches'] = 'all'
                trial = [deepcopy(op) for i, op in enumerate(original) if i not in removed]
                trial.insert(min(indices), merged)
                if self._verified_operations(before, after, trial):
                    self.warnings.append(f'Optimized {len(indices)} identical operations into exact union path {union}.')
                    return self._optimize_identical_paths(before, after, trial)
        return original

    @staticmethod
    def _node_at_tokens(root: Any, tokens: list[Any]) -> Any:
        node = root
        for token in tokens:
            if token == '*' or isinstance(token, tuple):
                return None
            try:
                node = node[token]
            except (KeyError, IndexError, TypeError):
                return None
        return node

    def _typed_wildcard_path(self, before: Any, paths: list[str]) -> str | None:
        """Build a wildcard path while preserving mapping/list intent.

        Mapping fan-out is rendered as ``*`` and list fan-out as ``[*]``.
        This is a readability-only distinction; both selectors are supported by
        the engine, but the typed form is easier for users to understand.
        """
        if len(paths) < 2:
            return None
        token_sets = [parse_path(path) for path in paths]
        length = len(token_sets[0])
        if length == 0 or any(len(tokens) != length for tokens in token_sets):
            return None
        varying = [pos for pos in range(length) if len({tokens[pos] for tokens in token_sets}) > 1]
        if not varying:
            return None
        encoded: list[str] = []
        base = token_sets[0]
        for pos, token in enumerate(base):
            if pos not in varying:
                raw_parts = [path[2:].split('/') if path.startswith('$/') else path.lstrip('/').split('/') for path in paths]
                if all(len(parts) > pos and parts[pos] == '[*]' for parts in raw_parts):
                    encoded.append('[*]')
                else:
                    encoded.append(str(token).replace('~', '~0').replace('/', '~1'))
                continue
            parent_types = []
            for tokens in token_sets:
                parent = self._node_at_tokens(before, list(tokens[:pos]))
                parent_types.append(type(parent))
            encoded.append('[*]' if parent_types and all(t is list for t in parent_types) else '*')
        return '$/' + '/'.join(encoded)

    def _final_large_outer_checkpoint(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Guaranteed bounded outer merge for large compiles.

        The normal optimizer is optional and deadline-bound. This final checkpoint
        tries the already-indexed semantic outer groups once from the latest verified
        state. It uses fast replay and never discards prior inner optimizations.
        """
        proposals = self._large_outer_proposals(operations)
        if not proposals:
            return operations
        trial = self._apply_large_proposals(operations, proposals)
        if self._cached_replay(before, after, trial):
            self.warnings.append(f'Final large outer checkpoint merged {len(operations)} operations into {len(trial)}.')
            return trial
        current = deepcopy(operations)
        accepted = 0
        for proposal in proposals[:16]:
            trial = self._apply_large_proposals(current, [proposal])
            if self._cached_replay(before, after, trial):
                current = trial
                accepted += 1
        if accepted:
            self.warnings.append(f'Final large outer checkpoint accepted {accepted} safe semantic group(s).')
        return current

    @staticmethod
    def _group_paths_by_shape(paths: list[str]) -> list[list[str]]:
        """Partition concrete/selector paths into compatible compression groups.

        Length is the primary shape boundary. This lets a single operation retain
        multiple compact patterns, e.g. one for nested mappings and another for a
        shallower list branch, instead of keeping hundreds of concrete entries.
        """
        groups: dict[int, list[str]] = {}
        for path in paths:
            groups.setdefault(len(parse_path(path)), []).append(path)
        return [group for _, group in sorted(groups.items())]

    def _compressed_paths_patterns(self, before: Any, paths: list[str]) -> list[str]:
        result: list[str] = []
        for group in self._group_paths_by_shape(paths):
            if len(group) == 1:
                result.extend(group)
                continue
            wildcard = self._typed_wildcard_path(before, group)
            if wildcard is not None:
                result.append(wildcard)
                continue
            union = self._union_path(group)
            if union is not None:
                result.append(union)
                continue
            result.extend(group)
        return list(dict.fromkeys(result))

    def _optimize_paths_to_single_path(self, before: Any, after: Any, operations: list[dict[str, Any]], *, final_pass: bool = False) -> list[dict[str, Any]]:
        """Compress explicit ``paths`` into one readable selector.

        The final pass is intentionally independent from the optional optimizer
        deadline. Large compiles often consume that budget before reaching this
        cheap cleanup stage. It remains bounded by operation count and at most one
        replay per ``paths`` operation, preserving termination guarantees.
        """
        current = deepcopy(operations)
        attempts = 0
        max_attempts = max(1, min(256, sum(1 for op in current if isinstance(op.get('paths'), list))))
        for index in range(len(current)):
            if attempts >= max_attempts:
                break
            raw = current[index]
            values = raw.get('paths')
            if not isinstance(values, list) or len(values) < 2:
                continue
            paths = list(dict.fromkeys(str(x) for x in values))
            compressed_patterns = self._compressed_paths_patterns(before, paths)
            if compressed_patterns != paths:
                attempts += 1
                merged = deepcopy(raw)
                if len(compressed_patterns) == 1:
                    merged.pop('paths', None)
                    merged['path'] = compressed_patterns[0]
                else:
                    merged.pop('path', None)
                    merged['paths'] = compressed_patterns
                if any('*' in path or '[' in path for path in compressed_patterns):
                    merged['on_multiple_matches'] = 'all'
                trial = deepcopy(current)
                trial[index] = merged
                if self._cached_replay(before, after, trial):
                    current = trial
                    self.warnings.append(
                        f'Compressed {len(paths)} paths into {len(compressed_patterns)} selector pattern(s).'
                    )
                    continue
            candidates: list[str] = []
            wildcard = self._typed_wildcard_path(before, paths)
            if wildcard:
                candidates.append(wildcard)
            union = self._union_path(paths)
            if union and union not in candidates:
                candidates.append(union)
            for candidate_path in candidates:
                attempts += 1
                merged = deepcopy(raw)
                merged.pop('paths', None)
                merged['path'] = candidate_path
                if '*' in candidate_path or '[' in candidate_path:
                    merged['on_multiple_matches'] = 'all'
                trial = deepcopy(current)
                trial[index] = merged
                if self._cached_replay(before, after, trial):
                    current = trial
                    self.warnings.append(f'Compressed paths into single path {candidate_path}.')
                    break
        return current

    @staticmethod
    def _merge_disjoint_mapping(left: Any, right: Any) -> Any | None:
        """Merge nested mappings only when overlapping leaves are identical.

        Returning ``None`` means the two payloads conflict and must remain as
        separate operations. Lists are intentionally not combined because their
        order is semantic.
        """
        if left is None:
            return deepcopy(right)
        if right is None:
            return deepcopy(left)
        if not isinstance(left, dict) or not isinstance(right, dict):
            return deepcopy(left) if left == right else None
        result = deepcopy(left)
        for key, value in right.items():
            if key not in result:
                result[key] = deepcopy(value)
                continue
            merged = DiffCompiler._merge_disjoint_mapping(result[key], value)
            if merged is None:
                return None
            result[key] = merged
        return result

    def _optimize_same_target_updates(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Combine compatible update_item blocks on the exact same target.

        This pass never moves an update across another operation. Only adjacent
        update_item blocks with equal path/paths, match and safety policy are
        considered. Their set/merge payloads and nested item_operations are joined
        when there are no conflicting writes, then strict replay decides safety.
        """
        current = deepcopy(operations)
        index = 0
        while index + 1 < len(current):
            first, second = current[index], current[index + 1]
            if first.get('op') != 'update_item' or second.get('op') != 'update_item':
                index += 1
                continue
            identity_fields = ('path', 'paths', 'match', 'missing', 'expect_matches', 'on_multiple_matches')
            if any(self._semantic_normalize(first.get(k)) != self._semantic_normalize(second.get(k)) for k in identity_fields):
                index += 1
                continue
            merged = deepcopy(first)
            ok = True
            for key in ('set', 'merge'):
                left_payload = merged.get(key)
                right_payload = second.get(key)
                if left_payload is None and right_payload is None:
                    merged.pop(key, None)
                    continue
                payload = self._merge_disjoint_mapping(left_payload, right_payload)
                if payload is None:
                    ok = False
                    break
                if payload:
                    merged[key] = payload
                else:
                    merged.pop(key, None)
            if not ok:
                index += 1
                continue
            nested = list(deepcopy(merged.get('item_operations') or []))
            seen = {self._semantic_fingerprint(item) for item in nested}
            for item in second.get('item_operations') or []:
                fingerprint = self._semantic_fingerprint(item)
                if fingerprint not in seen:
                    nested.append(deepcopy(item))
                    seen.add(fingerprint)
            if nested:
                merged['item_operations'] = nested
            elif 'item_operations' in merged:
                merged.pop('item_operations', None)
            trial = current[:index] + [merged] + current[index + 2:]
            if self._cached_replay(before, after, trial):
                current = trial
                self.warnings.append('Merged adjacent compatible update_item operations on the same target.')
                continue
            index += 1
        return current

    def _remove_redundant_paths(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove paths entries whose concrete targets are covered by another entry."""
        current = deepcopy(operations)
        for index, operation in enumerate(list(current)):
            values = operation.get('paths')
            if not isinstance(values, list) or len(values) < 2:
                continue
            entries = list(dict.fromkeys(str(v) for v in values))
            expanded: list[set[str]] = []
            for entry in entries:
                try:
                    targets = set(expand_paths(before, entry))
                except Exception:
                    targets = set()
                expanded.append(targets)
            keep = [True] * len(entries)
            for i, targets in enumerate(expanded):
                if not targets:
                    continue
                for j, other in enumerate(expanded):
                    if i == j or not other:
                        continue
                    if targets <= other and (targets < other or len(entries[j]) <= len(entries[i])):
                        keep[i] = False
                        break
            reduced = [entry for entry, flag in zip(entries, keep) if flag]
            if not reduced or reduced == entries:
                continue
            merged = deepcopy(operation)
            if len(reduced) == 1:
                merged.pop('paths', None)
                merged['path'] = reduced[0]
            else:
                merged['paths'] = reduced
            trial = deepcopy(current)
            trial[index] = merged
            if self._cached_replay(before, after, trial):
                current = trial
                self.warnings.append(f'Removed {len(entries) - len(reduced)} redundant paths entry/entries.')
        return current

    def _optimize_parent_merges(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Combine sibling set/replace operations under one parent into merge.

        Example: ``$/opt/*/timeout`` and ``$/opt/*/retry`` become one merge at
        ``$/opt/*``. The candidate is accepted only after full replay verification,
        so ordering or overwrite differences automatically keep the original ops.
        """
        original = deepcopy(operations)
        groups: dict[str, list[int]] = {}
        for index, operation in enumerate(original):
            if operation.get('op') not in {'set', 'replace'}:
                continue
            path = operation.get('path')
            if not isinstance(path, str) or path in {'$', '/'}:
                continue
            tokens = parse_path(path)
            if len(tokens) < 1 or not isinstance(tokens[-1], str) or tokens[-1] == '*':
                continue
            parent_tokens = tokens[:-1]
            encoded = [str(token).replace('~', '~0').replace('/', '~1') for token in parent_tokens]
            parent = '$' if not encoded else '$/' + '/'.join(encoded)
            groups.setdefault(parent, []).append(index)
        for parent, indices in groups.items():
            if len(indices) < 2:
                continue
            value: dict[str, Any] = {}
            keys: set[str] = set()
            valid = True
            for index in indices:
                tokens = parse_path(str(original[index]['path']))
                key = str(tokens[-1])
                if key in keys:
                    valid = False; break
                keys.add(key)
                value[key] = deepcopy(original[index].get('value'))
            if not valid:
                continue
            candidate = {
                'op': 'merge', 'path': parent, 'value': value,
                'missing': 'skip', 'on_multiple_matches': 'all',
            }
            removed = set(indices)
            trial = [deepcopy(op) for i, op in enumerate(original) if i not in removed]
            trial.insert(min(indices), candidate)
            if not self._verified_operations(before, after, trial):
                continue
            self.warnings.append(
                f'Merged {len(indices)} sibling operations into one parent merge at {parent}.'
            )
            return self._optimize_parent_merges(before, after, trial)
        return original

    @staticmethod
    def _join(path:str,key:str)->str:
        escaped = str(key).replace('~', '~0').replace('/', '~1')
        if path == '$':
            return f'$/{escaped}'
        if path.startswith('$/') or path.startswith('/'):
            return f'{path}/{escaped}'
        # Existing user-provided dot paths remain accepted, but generated paths use JSON Pointer.
        return f'{path}/{escaped}'

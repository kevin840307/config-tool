from __future__ import annotations
from dataclasses import dataclass
from copy import deepcopy
import re
from typing import Any
from .engine import YamlPatchEngine
from .comparison import strict_equal
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

    def __init__(self, identity_rules: dict[str, list[str]] | None = None, *, retry_protection: bool = False, readable: bool = True) -> None:
        self.identity_rules = identity_rules or {}
        self.retry_protection = retry_protection
        self.readable = readable
        self.warnings: list[str] = []

    def compile(self, before: Any, after: Any, variables: dict[str, Any] | None = None) -> CompileResult:
        self.warnings = []
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
        config = {'version': 1, 'variables': variables or {}, 'options': {'atomic_write': True}, 'operations': ops}
        result = YamlPatchEngine().apply_document(deepcopy(before), config)
        verified = strict_equal(result, after)
        strategy = 'semantic' if verified else 'fallback-replace'
        if not verified:
            self.warnings.append('Semantic operations did not reproduce the target; replaced the full document.')
            config['operations'] = [{'id': 'replace-document', 'op': 'replace', 'path': '$', 'value': deepcopy(after)}]
            result = YamlPatchEngine().apply_document(deepcopy(before), config)
        return CompileResult(config, strict_equal(result, after), strategy, list(self.warnings))


    def _candidate_replays(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> bool:
        try:
            config = {'version': 1, 'options': {'atomic_write': True}, 'operations': deepcopy(operations)}
            return strict_equal(YamlPatchEngine().apply_document(deepcopy(before), config), after)
        except Exception:
            return False

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
            result.append(op)
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

    def _diff_mapping(self, a: dict[str,Any], b: dict[str,Any], path: str, ops: list[dict[str,Any]]) -> None:
        akeys, bkeys = list(a), list(b)
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
        # Run the optimizer to a verified fixed point. A successful pass can expose
        # new candidates for the next pass (for example exact paths -> wildcard
        # paths -> one parent-level merge). The round limit prevents pathological
        # configs from spending unbounded time in optimization.
        max_rounds = 6
        for round_no in range(1, max_rounds + 1):
            before_round = repr(current)
            passes = []
            if len(current) <= 500:
                passes.extend([self._optimize_common_insert_keys, self._optimize_common_item_operations])
            elif round_no == 1:
                self.warnings.append(
                    f'Skipped high-cost partial common-difference extraction for {len(current)} operations; linear verified passes remain enabled.'
                )
            passes.extend([self._optimize_identical_paths, self._optimize_parent_merges])
            for optimizer in passes:
                checkpoint = deepcopy(current)
                try:
                    candidate = optimizer(before, after, current)
                    if candidate == checkpoint:
                        current = checkpoint
                    elif self._verified_operations(before, after, candidate) and self._is_better(candidate, checkpoint):
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

    def _verified_operations(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> bool:
        cfg = {'version': 1, 'options': {'atomic_write': True}, 'operations': operations}
        try:
            actual = YamlPatchEngine().apply_document(deepcopy(before), cfg)
        except Exception:
            return False
        return strict_equal(actual, after)

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
        encoded = [str(token).replace('~', '~0').replace('/', '~1') for token in candidate]
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

    def _best_exact_selector_path(self, before: Any, paths: list[str]) -> str | None:
        # Current-state first: when the selected paths cover every current child,
        # prefer the shorter wildcard. Only use an exact union for a partial set.
        wildcard = self._wildcard_path(paths)
        if wildcard is not None and set(expand_paths(before, wildcard)) == set(paths):
            return wildcard
        union = self._union_path(paths)
        if union is not None and set(expand_paths(before, union)) == set(paths):
            return union
        return None

    def _optimize_common_insert_keys(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract identical added keys/sections under sibling mappings.

        Example: three ``insert_key`` operations adding the same ``retry`` mapping
        become one ``set`` at ``$/opt/*/retry`` with ``missing:create``. Individual
        additions that differ remain untouched.
        """
        original = deepcopy(operations)
        groups: dict[str, list[int]] = {}
        for index, operation in enumerate(original):
            if operation.get('op') != 'insert_key':
                continue
            path, key = operation.get('path'), operation.get('key')
            if not isinstance(path, str) or not isinstance(key, str) or '*' in path:
                continue
            signature = {
                'key': key,
                'value': deepcopy(operation.get('value')),
            }
            groups.setdefault(repr(signature), []).append(index)

        for indices in groups.values():
            if len(indices) < 2:
                continue
            parent_paths = [str(original[index]['path']) for index in indices]
            wildcard_parent = self._best_exact_selector_path(before, parent_paths)
            if wildcard_parent is None:
                continue
            # The wildcard must identify exactly the original sibling parents.
            if set(expand_paths(before, wildcard_parent)) != set(parent_paths):
                continue
            key = str(original[indices[0]]['key'])
            candidate = {
                'op': 'set',
                'path': self._join(wildcard_parent, key),
                'value': deepcopy(original[indices[0]].get('value')),
                'missing': 'create',
                'on_multiple_matches': 'all',
            }
            trial = [deepcopy(op) for i, op in enumerate(original) if i not in set(indices)]
            trial.insert(min(indices), candidate)
            if not self._verified_operations(before, after, trial):
                continue
            self.warnings.append(
                f'Extracted common added section {key!r} from {len(indices)} sibling mappings into {candidate["path"]}.'
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
                if not self._verified_operations(before, after, trial):
                    continue
                self.warnings.append(
                    f'Extracted a common list-item change from {len(participating)} items into wildcard path {direct["path"]}.'
                )
                return self._optimize_common_item_operations(before, after, trial)
        return original

    def _optimize_identical_paths(self, before: Any, after: Any, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge otherwise identical direct operations into wildcard selectors."""
        original = deepcopy(operations)
        groups: dict[str, list[int]] = {}
        for idx, op in enumerate(original):
            if op.get('op') not in {'replace', 'set', 'replace_value', 'remove', 'insert_key', 'copy_item', 'remove_item', 'update_item', 'move_item'}:
                continue
            path = op.get('path')
            if not isinstance(path, str) or '*' in path:
                continue
            signature = deepcopy(op)
            signature.pop('path', None); signature.pop('id', None)
            groups.setdefault(repr(signature), []).append(idx)

        replacements: dict[int, tuple[set[int], dict[str, Any], str]] = {}
        occupied: set[int] = set()
        for indices in groups.values():
            if len(indices) < 2 or any(i in occupied for i in indices):
                continue
            paths = [str(original[i]['path']) for i in indices]
            candidate_path = self._best_exact_selector_path(before, paths)
            if candidate_path is None:
                continue
            if set(expand_paths(before, candidate_path)) != set(paths):
                continue
            merged = deepcopy(original[indices[0]])
            merged['path'] = candidate_path
            merged['on_multiple_matches'] = 'all'
            removal = set(indices)
            replacements[min(indices)] = (removal, merged, candidate_path)
            occupied.update(removal)

        if not replacements:
            return original
        trial: list[dict[str, Any]] = []
        skipped: set[int] = set()
        for index, operation in enumerate(original):
            if index in skipped:
                continue
            if index in replacements:
                removal, merged, _ = replacements[index]
                trial.append(merged)
                skipped.update(removal - {index})
            else:
                trial.append(operation)
        if not self._verified_operations(before, after, trial):
            return original
        for removal, _, candidate_path in replacements.values():
            self.warnings.append(f'Optimized {len(removal)} identical operations into selector path {candidate_path}.')
        return trial

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

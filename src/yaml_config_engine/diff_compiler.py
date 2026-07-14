from __future__ import annotations
from dataclasses import dataclass
from copy import deepcopy
import re
from typing import Any
from .engine import YamlPatchEngine
from .comparison import strict_equal

@dataclass
class CompileResult:
    config: dict[str, Any]
    verified: bool
    strategy: str
    warnings: list[str]

class DiffCompiler:
    ID_KEYS = ('name','id','key','code','version')

    def __init__(self, identity_rules: dict[str, list[str]] | None = None) -> None:
        self.identity_rules = identity_rules or {}
        self.warnings: list[str] = []

    def compile(self, before: Any, after: Any, variables: dict[str, Any] | None = None) -> CompileResult:
        self.warnings = []
        ops: list[dict[str, Any]] = []
        try:
            self._diff(before, after, '$', ops)
        except Exception as exc:
            self.warnings.append(f'Semantic compiler fallback after {type(exc).__name__}: {exc}')
            ops = [{'id': 'replace-document', 'op': 'replace', 'path': '$', 'value': deepcopy(after)}]
        config = {'version': 1, 'variables': variables or {}, 'options': {'atomic_write': True}, 'operations': ops}
        result = YamlPatchEngine().apply_document(deepcopy(before), config)
        verified = strict_equal(result, after)
        strategy = 'semantic' if verified else 'fallback-replace'
        if not verified:
            self.warnings.append('Semantic operations did not reproduce the target; replaced the full document.')
            config['operations'] = [{'id': 'replace-document', 'op': 'replace', 'path': '$', 'value': deepcopy(after)}]
            result = YamlPatchEngine().apply_document(deepcopy(before), config)
        return CompileResult(config, strict_equal(result, after), strategy, list(self.warnings))

    def _diff(self, a: Any, b: Any, path: str, ops: list[dict[str, Any]]) -> None:
        if type(a) is not type(b):
            ops.append({'op':'replace','path':path,'value':deepcopy(b)}); return
        if isinstance(a, dict):
            self._diff_mapping(a,b,path,ops)
        elif isinstance(a, list):
            self._diff_list(a,b,path,ops)
        elif a != b:
            ops.append({'op':'replace','path':path,'value':deepcopy(b)})

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
                    copy_op={'op':'copy_item','path':path,'source':{'match':dict(zip(keys,clone_id)),'expect_matches':1},'set':set_values,'position':position,'duplicate':{'unique_by':keys,'policy':'skip_if_equal'}}
                    if item_operations:
                        copy_op['item_operations']=item_operations
                    ops.append(copy_op)
                else:
                    ops.append({'op':'insert','path':path,'position':position,'value':deepcopy(item),
                                'duplicate':{'unique_by':keys,'policy':'skip_if_equal'}})
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
    def _join(path:str,key:str)->str:
        escaped = str(key).replace('~', '~0').replace('/', '~1')
        if path == '$':
            return f'$/{escaped}'
        if path.startswith('$/') or path.startswith('/'):
            return f'{path}/{escaped}'
        # Existing user-provided dot paths remain accepted, but generated paths use JSON Pointer.
        return f'{path}/{escaped}'

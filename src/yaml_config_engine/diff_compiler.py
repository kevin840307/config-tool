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
                ops.append({'op':'rename_key','path':path,'old_key':old_key,'new_key':new_key})
                value = a.pop(old_key)
                idx = bkeys.index(new_key)
                if hasattr(a, 'insert'): a.insert(min(idx, len(a)), new_key, value)
                else: a[new_key] = value
        for k in removed:
            ops.append({'op':'remove','path':self._join(path,k)})
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
        # Enforce exact mapping order after rename/add/remove. Generated configs use
        # relative keys, never mapping indexes. Moving in target order is deterministic.
        if akeys != bkeys or renamed or removed or added:
            for idx, key in enumerate(bkeys):
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
                    copy_op={'op':'copy_item','path':path,'source':{'match':dict(zip(keys,clone_id)),'expect_matches':1},'set':set_values,'position':position,'duplicate':{'unique_by':keys,'policy':'error'}}
                    if item_operations:
                        copy_op['item_operations']=item_operations
                    ops.append(copy_op)
                else:
                    ops.append({'op':'insert','path':path,'position':position,'value':deepcopy(item)})
        for ident in aid.keys()-bid.keys():
            ops.append({'op':'remove_item','path':path,'match':dict(zip(keys,ident)),'expect_matches':1})
        for ident in aid.keys() & bid.keys():
            old,new=aid[ident],bid[ident]
            changes={k:deepcopy(v) for k,v in new.items() if old.get(k)!=v}
            removed=[k for k in old if k not in new]
            if changes or removed:
                ops.append({'op':'update_item','path':path,'match':dict(zip(keys,ident)),'set':changes,'remove':removed,'expect_matches':1})
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

    @staticmethod
    def _find_clone(amap:dict[tuple[Any,...],dict[str,Any]],item:dict[str,Any],keys:list[str]) -> tuple[tuple[Any,...],dict[str,Any],list[dict[str,Any]]] | None:
        """Find a safe clone source, including one-to-one dynamic key renames.

        Returns (source identity, scalar overrides, item operations). Only a
        unique best candidate is accepted, avoiding ambiguous compiler output.
        """
        candidates=[]
        for ident,old in amap.items():
            old_non={k:v for k,v in old.items() if k not in keys}
            new_non={k:v for k,v in item.items() if k not in keys}
            if old_non==new_non:
                candidates.append((0, DiffCompiler._version_like_distance(old, item, None, None, keys), ident, {k:deepcopy(item[k]) for k in keys if old.get(k)!=item.get(k)}, []))
                continue
            removed=[k for k in old_non if k not in new_non]
            added=[k for k in new_non if k not in old_non]
            common=[k for k in old_non if k in new_non]
            changes={k:deepcopy(item[k]) for k in keys if old.get(k)!=item.get(k)}
            changes.update({k:deepcopy(new_non[k]) for k in common if old_non[k]!=new_non[k]})
            item_ops=[]
            if len(removed)==len(added)==1 and old_non[removed[0]]==new_non[added[0]]:
                item_ops=[{'op':'rename_key','path':'$','old_key':removed[0],'new_key':added[0]}]
                score=len(changes)+1
                distance = DiffCompiler._version_like_distance(old, item, removed[0], added[0], keys)
                candidates.append((score,distance,ident,changes,item_ops))
        if not candidates:
            return None
        candidates.sort(key=lambda x:(x[0], x[1]))
        if len(candidates)>1 and candidates[0][:2]==candidates[1][:2]:
            return None
        _,_,ident,changes,item_ops=candidates[0]
        return ident,changes,item_ops

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

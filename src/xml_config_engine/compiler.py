from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from yaml_config_engine.models import omit_concise_defaults
import xml.etree.ElementTree as ET
import re
import os
import time

from .engine import XmlPatchEngine

@dataclass
class XmlCompileResult:
    config: dict[str, Any]
    verified: bool
    strategy: str
    warnings: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.split('}',1)[-1]


def _path_seg(node: ET.Element, siblings: list[ET.Element]) -> str:
    name=_local(node.tag)
    same=[x for x in siblings if _local(x.tag)==name]
    if len(same)==1: return name
    for key in ('id','name','key','code','type'):
        val=node.attrib.get(key)
        if val is not None and sum(1 for x in same if x.attrib.get(key)==val)==1:
            return f'{name}[@{key}="{val}"]'
    return f'{name}[{same.index(node)+1}]'



def _is_numeric_like_scalar(value: str) -> bool:
    """Return True for complete numeric/version-like text such as 30 or 2026.05."""
    import re
    return re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)", value.strip()) is not None


def _string_replacement(old: str, new: str) -> dict[str, Any] | None:
    if old == new:
        return None
    if _is_numeric_like_scalar(old) or _is_numeric_like_scalar(new):
        return None
    prefix = 0
    limit = min(len(old), len(new))
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < len(old)-prefix and suffix < len(new)-prefix and
           old[len(old)-1-suffix] == new[len(new)-1-suffix]):
        suffix += 1
    old_mid = old[prefix:len(old)-suffix if suffix else len(old)]
    new_mid = new[prefix:len(new)-suffix if suffix else len(new)]
    if not old_mid or old.count(old_mid) != 1:
        return None
    if old.replace(old_mid, new_mid, 1) != new:
        return None
    return {'search': old_mid, 'replacement': new_mid, 'count': 1, 'expect_replacements': 1}

def _mixed(e: ET.Element) -> bool:
    if len(e)==0: return False
    if (e.text or '').strip(): return True
    return any((c.tail or '').strip() for c in e)


class XmlDiffCompiler:
    def compile_files(self, before: str|Path, after: str|Path) -> XmlCompileResult:
        before_bytes = Path(before).read_bytes()
        after_bytes = Path(after).read_bytes()
        # Decode bytes directly rather than Path.read_text so CRLF is not
        # normalized by universal-newline handling.
        btxt = before_bytes.decode('utf-8-sig')
        atxt = after_bytes.decode('utf-8-sig')
        result = self.compile_text(btxt, atxt)
        if result.config.get('xml_action') == 'replace_entire_file':
            result.config['xml_utf8_bom'] = after_bytes.startswith(b'\xef\xbb\xbf')
        return result

    def compile_text(self,btxt:str,atxt:str)->XmlCompileResult:
        warnings=[]
        try:
            b=ET.fromstring(btxt); a=ET.fromstring(atxt)
            if _local(b.tag)!=_local(a.tag): raise ValueError('root element changed')
            ops=[]; self._diff(b,a,'/'+_local(b.tag),ops,warnings)
            ops=self._optimize_selectors(btxt,atxt,ops,warnings)
            ops=omit_concise_defaults(ops)
            cfg={'version':1,'defaults_profile':'concise-v1','format':'xml','options':{'atomic_write':True},'operations':ops}
            actual=XmlPatchEngine().apply_text(btxt,cfg)[0]
            if actual == atxt:
                return XmlCompileResult(cfg,True,'structural-operations-exact',warnings)
            warnings.append('Target formatting/prolog/comments differ from the source-preserving result; exact-text fallback used.')
        except Exception as exc:
            warnings.append(f'Structural compiler fallback: {exc}')
        return XmlCompileResult({'version':1,'format':'xml','options':{'atomic_write':True},'xml_action':'replace_entire_file','xml_exact_text':atxt,'operations':[]},True,'replace-entire-file-exact',warnings)


    @staticmethod
    def _selector_candidates(paths: list[str]) -> list[str]:
        """Return selector candidates in the required simplification order.

        Prefer the shortest wildcard first. Full replay verification is the final
        authority. Exact unions are only a fallback when a wildcard would also
        modify unrelated sibling nodes.
        """
        parts = [path.strip('/').split('/') for path in paths]
        if not parts or any(len(item) != len(parts[0]) for item in parts):
            return []
        varying = [pos for pos in range(len(parts[0])) if len({item[pos] for item in parts}) > 1]
        if not varying:
            return []
        candidates: list[str] = []
        wildcard = list(parts[0])
        for pos in varying:
            wildcard[pos] = '*'
        candidates.append('/' + '/'.join(wildcard))

        # Exact name union is deliberately second: it preserves a concise single
        # operation when wildcard replay is too broad, without overriding the
        # user's wildcard-first simplification preference.
        if len(varying) == 1:
            pos = varying[0]
            values = []
            for item in parts:
                value = item[pos]
                if value not in values:
                    values.append(value)
            if all(re.fullmatch(r'[A-Za-z_][\w:.-]*', value) for value in values):
                union = list(parts[0])
                union[pos] = '[' + ','.join(values) + ']'
                union_path = '/' + '/'.join(union)
                if union_path not in candidates:
                    candidates.append(union_path)
        return candidates

    @staticmethod
    def _verified_trial(before_text: str, after_text: str, operations: list[dict[str, Any]]) -> bool:
        cfg = {'version': 1, 'defaults_profile': 'concise-v1', 'format': 'xml', 'options': {'atomic_write': True}, 'operations': operations}
        try:
            return XmlPatchEngine().apply_text(before_text, cfg)[0] == after_text
        except Exception:
            return False

    @staticmethod
    def _operation_fingerprint(value: Any) -> Any:
        if isinstance(value, dict):
            return ('dict', tuple((XmlDiffCompiler._operation_fingerprint(k), XmlDiffCompiler._operation_fingerprint(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return ('list', tuple(XmlDiffCompiler._operation_fingerprint(v) for v in value))
        return ('scalar', type(value).__module__, type(value).__qualname__, repr(value))

    @staticmethod
    def _optimize_selectors(before_text: str, after_text: str, operations: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
        current = [dict(op) for op in operations]
        timeout_seconds = float(os.getenv('CONFIG_TOOL_OPTIMIZATION_TIMEOUT_SECONDS', '5'))
        max_candidates = int(os.getenv('CONFIG_TOOL_OPTIMIZATION_MAX_CANDIDATES', '2000'))
        deadline = time.monotonic() + max(0.05, timeout_seconds)
        candidate_count = 0
        limit_reported = False
        seen_states: set[Any] = set()
        replay_cache: dict[Any, bool] = {}

        def budget_exhausted() -> bool:
            nonlocal limit_reported
            exhausted = time.monotonic() >= deadline or candidate_count >= max_candidates
            if exhausted and not limit_reported:
                reason = 'time budget' if time.monotonic() >= deadline else 'candidate limit'
                warnings.append(f'XML config optimization stopped at the {reason}; kept the last replay-verified config (candidates={candidate_count}, limit={max_candidates}, timeout={timeout_seconds:g}s).')
                limit_reported = True
            return exhausted

        def checked_trial(trial: list[dict[str, Any]]) -> bool:
            nonlocal candidate_count
            key = XmlDiffCompiler._operation_fingerprint(trial)
            if key in replay_cache:
                return replay_cache[key]
            if budget_exhausted():
                return False
            candidate_count += 1
            verified = XmlDiffCompiler._verified_trial(before_text, after_text, trial)
            replay_cache[key] = verified
            return verified

        # Fixed-point optimization is required because one exact path merge can
        # expose a second parent-level merge. Operations with different semantic
        # fields are never grouped; they remain as independent residual entries.
        for _round in range(8):
            if budget_exhausted():
                break
            state = XmlDiffCompiler._operation_fingerprint(current)
            if state in seen_states:
                warnings.append(f'XML config optimization stopped because a repeated state was detected at round {_round + 1}.')
                break
            seen_states.add(state)
            changed = False

            # Identical inserted child sections under related parents.
            insert_groups: dict[str, list[int]] = {}
            for index, operation in enumerate(current):
                if operation.get('op') != 'insert_key':
                    continue
                path, key = operation.get('path'), operation.get('key')
                if not isinstance(path, str) or not isinstance(key, str) or '*' in path or '/[' in path:
                    continue
                signature = dict(operation)
                signature.pop('path', None); signature.pop('id', None)
                insert_groups.setdefault(repr(signature), []).append(index)
            for indices in insert_groups.values():
                if len(indices) < 2:
                    continue
                parent_paths = [str(current[index]['path']) for index in indices]
                key = str(current[indices[0]]['key'])
                for parent_selector in XmlDiffCompiler._selector_candidates(parent_paths):
                    candidate_path = parent_selector.rstrip('/') + '/' + key
                    merged = {
                        'op': 'set', 'path': candidate_path,
                        'value': current[indices[0]].get('value'),
                        'missing': 'create', 'on_multiple_matches': 'all',
                    }
                    removed = set(indices)
                    trial = [dict(op) for index, op in enumerate(current) if index not in removed]
                    trial.insert(min(indices), merged)
                    if checked_trial(trial):
                        current = trial
                        changed = True
                        warnings.append(f'Extracted {len(indices)} identical XML child additions into {candidate_path}.')
                        break
                if changed:
                    break
            if changed:
                continue

            # Same operation semantics, only path differs. This handles set,
            # replace, replace_value and remove while preserving all non-path
            # options such as missing, search/count and match expectations.
            groups: dict[str, list[int]] = {}
            for index, operation in enumerate(current):
                if operation.get('op') not in {'set', 'replace', 'replace_value', 'remove'}:
                    continue
                path = operation.get('path')
                if not isinstance(path, str) or '*' in path or '/[' in path:
                    continue
                signature = dict(operation)
                signature.pop('path', None); signature.pop('id', None)
                groups.setdefault(repr(signature), []).append(index)
            for indices in groups.values():
                if len(indices) < 2:
                    continue
                paths = [str(current[index]['path']) for index in indices]
                removed = set(indices)
                candidates = XmlDiffCompiler._selector_candidates(paths)
                wildcard = next((candidate for candidate in candidates if '*' in candidate), None)
                union = next((candidate for candidate in candidates if '/[' in candidate), None)
                if wildcard is not None:
                    merged = dict(current[indices[0]])
                    merged['path'] = wildcard
                    merged.pop('paths', None)
                    merged['on_multiple_matches'] = 'all'
                    trial = [dict(op) for index, op in enumerate(current) if index not in removed]
                    trial.insert(min(indices), merged)
                    if checked_trial(trial):
                        current = trial; changed = True
                        warnings.append(f'Optimized {len(indices)} identical XML operations into wildcard path {wildcard}.')
                if not changed:
                    merged = dict(current[indices[0]])
                    merged.pop('path', None)
                    merged['paths'] = list(dict.fromkeys(paths))
                    trial = [dict(op) for index, op in enumerate(current) if index not in removed]
                    trial.insert(min(indices), merged)
                    if checked_trial(trial):
                        current = trial; changed = True
                        warnings.append(f'Optimized {len(indices)} identical XML operations into one paths operation.')
                if not changed and union is not None:
                    merged = dict(current[indices[0]])
                    merged['path'] = union
                    merged.pop('paths', None)
                    merged['on_multiple_matches'] = 'all'
                    trial = [dict(op) for index, op in enumerate(current) if index not in removed]
                    trial.insert(min(indices), merged)
                    if checked_trial(trial):
                        current = trial; changed = True
                        warnings.append(f'Optimized {len(indices)} identical XML operations into exact union path {union}.')
                if changed:
                    break
            if not changed:
                break
        return current

    def _diff(self,b:ET.Element,a:ET.Element,path:str,ops:list[dict[str,Any]],warnings:list[str]):
        if _mixed(b) or _mixed(a):
            if ET.tostring(b,encoding='unicode')!=ET.tostring(a,encoding='unicode'): raise ValueError(f'mixed content at {path}')
            return
        for k in b.attrib.keys()-a.attrib.keys(): ops.append({'op':'remove','path':path+f'/@{k}'})
        for k,v in a.attrib.items():
            if b.attrib.get(k)!=v:
                if k not in b.attrib: raise ValueError(f'new attribute {k} at {path}')
                replacement = _string_replacement(b.attrib.get(k, ''), v) if k in b.attrib else None
                if replacement is not None:
                    ops.append({'op':'replace_value','path':path+f'/@{k}',**replacement})
                else:
                    ops.append({'op':'set','path':path+f'/@{k}','value':v})
        bc=list(b); ac=list(a)
        if not bc and not ac:
            old_text=(b.text or '').strip(); new_text=(a.text or '').strip()
            if old_text != new_text:
                replacement = _string_replacement(old_text, new_text)
                if replacement is not None:
                    ops.append({'op':'replace_value','path':path,**replacement})
                else:
                    ops.append({'op':'set','path':path,'value':a.text or ''})
            return
        # conservative alignment by unique identity/path segment
        bmap={_path_seg(x,bc):x for x in bc}; amap={_path_seg(x,ac):x for x in ac}
        if len(bmap)!=len(bc) or len(amap)!=len(ac): raise ValueError(f'ambiguous repeated children at {path}')
        for seg in bmap.keys()-amap.keys(): ops.append({'op':'remove','path':path+'/'+seg})
        for idx,seg in enumerate(amap):
            if seg not in bmap:
                val=self._to_value(amap[seg]); pos={'last':True}
                if idx>0: pos={'after_key':_local(ac[idx-1].tag)}
                ops.append({'op':'insert_key','path':path,'key':_local(amap[seg].tag),'value':val,'position':pos})
            else: self._diff(bmap[seg],amap[seg],path+'/'+seg,ops,warnings)
        if list(bmap)!=list(amap): warnings.append(f'Child order changed at {path}; compiler preserves values but may fallback if order is significant.')

    def _to_value(self,e:ET.Element):
        if not list(e): return (e.text or '')
        d={}
        for c in e:
            key=_local(c.tag); val=self._to_value(c)
            if key in d: raise ValueError('repeated inserted child requires fallback')
            d[key]=val
        return d

    @staticmethod
    def _structural_equal(x:str,y:str)->bool:
        def canon(e): return (_local(e.tag),tuple(sorted(e.attrib.items())),(e.text or '').strip(),tuple(canon(c) for c in e))
        return canon(ET.fromstring(x))==canon(ET.fromstring(y))

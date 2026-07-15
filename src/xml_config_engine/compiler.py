from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

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
            cfg={'version':1,'format':'xml','options':{'atomic_write':True},'operations':ops}
            actual=XmlPatchEngine().apply_text(btxt,cfg)[0]
            if actual == atxt:
                return XmlCompileResult(cfg,True,'structural-operations-exact',warnings)
            warnings.append('Target formatting/prolog/comments differ from the source-preserving result; exact-text fallback used.')
        except Exception as exc:
            warnings.append(f'Structural compiler fallback: {exc}')
        return XmlCompileResult({'version':1,'format':'xml','options':{'atomic_write':True},'xml_action':'replace_entire_file','xml_exact_text':atxt,'operations':[]},True,'replace-entire-file-exact',warnings)


    @staticmethod
    def _optimize_selectors(before_text: str, after_text: str, operations: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
        current = [dict(op) for op in operations]

        # Extract identical newly-added child sections under sibling XML elements.
        # The candidate is accepted only when the exact source-preserving XML output
        # equals the requested target text.
        insert_groups: dict[str, list[int]] = {}
        for index, operation in enumerate(current):
            if operation.get('op') != 'insert_key':
                continue
            path, key = operation.get('path'), operation.get('key')
            if not isinstance(path, str) or not isinstance(key, str) or '*' in path:
                continue
            signature = repr({'key': key, 'value': operation.get('value')})
            insert_groups.setdefault(signature, []).append(index)
        for indices in insert_groups.values():
            if len(indices) < 2:
                continue
            paths = [str(current[index]['path']).strip('/').split('/') for index in indices]
            if not paths or any(len(parts) != len(paths[0]) for parts in paths):
                continue
            varying = [pos for pos in range(len(paths[0])) if len({parts[pos] for parts in paths}) > 1]
            if not varying:
                continue
            parent = list(paths[0])
            for pos in varying:
                parent[pos] = '*'
            key = str(current[indices[0]]['key'])
            candidate_path = '/' + '/'.join(parent + [key])
            merged = {
                'op': 'set', 'path': candidate_path,
                'value': current[indices[0]].get('value'),
                'missing': 'create', 'on_multiple_matches': 'all',
            }
            removed = set(indices)
            trial = [op for index, op in enumerate(current) if index not in removed]
            trial.insert(min(indices), merged)
            cfg = {'version': 1, 'format': 'xml', 'options': {'atomic_write': True}, 'operations': trial}
            try:
                actual = XmlPatchEngine().apply_text(before_text, cfg)[0]
            except Exception:
                continue
            if actual == after_text:
                current = trial
                warnings.append(f'Extracted common XML child section {key!r} into wildcard path {candidate_path}.')
                break

        changed = True
        while changed:
            changed = False
            groups: dict[str, list[int]] = {}
            for index, operation in enumerate(current):
                if operation.get('op') not in {'set','replace','replace_value','remove'}:
                    continue
                path = operation.get('path')
                if not isinstance(path, str) or '*' in path:
                    continue
                signature = dict(operation); signature.pop('path', None); signature.pop('id', None)
                groups.setdefault(repr(signature), []).append(index)
            for indices in groups.values():
                if len(indices) < 2:
                    continue
                paths = [str(current[index]['path']).strip('/').split('/') for index in indices]
                if not paths or any(len(parts) != len(paths[0]) for parts in paths):
                    continue
                varying = [pos for pos in range(len(paths[0])) if len({parts[pos] for parts in paths}) > 1]
                if not varying:
                    continue
                candidate = list(paths[0])
                for pos in varying:
                    candidate[pos] = '*'
                candidate_path = '/' + '/'.join(candidate)
                merged = dict(current[indices[0]])
                merged['path'] = candidate_path
                merged['on_multiple_matches'] = 'all'
                removed = set(indices)
                trial = [op for index, op in enumerate(current) if index not in removed]
                trial.insert(min(indices), merged)
                cfg={'version':1,'format':'xml','options':{'atomic_write':True},'operations':trial}
                try:
                    actual=XmlPatchEngine().apply_text(before_text,cfg)[0]
                except Exception:
                    continue
                if actual == after_text:
                    current=trial
                    changed=True
                    warnings.append(f'Optimized {len(indices)} identical XML operations into wildcard path {candidate_path}.')
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

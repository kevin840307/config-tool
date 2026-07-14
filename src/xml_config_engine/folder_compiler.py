from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import shutil, tempfile
from typing import Any
from yaml_config_engine.yamlio import dump_one, load_one
from .compiler import XmlDiffCompiler
from .engine import XmlPatchEngine

@dataclass
class XmlFolderCompileResult:
    manifest_path: Path
    entries: list[dict[str,Any]]
    verified: bool

class XmlFolderCompiler:
    def compile_folder(self,before_root,after_root,output_root,include_unchanged=False,verify=True):
        b=Path(before_root).resolve(); a=Path(after_root).resolve(); out=Path(output_root).resolve()
        if out.exists(): shutil.rmtree(out)
        out.mkdir(parents=True)
        rels=sorted({p.relative_to(b) for p in b.rglob('*.xml')}|{p.relative_to(a) for p in a.rglob('*.xml')})
        entries=[]; all_ok=True
        for rel in rels:
            bp=b/rel; ap=a/rel
            if not bp.exists():
                dst=out/'created'/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(ap,dst)
                entries.append({'relative_path':rel.as_posix(),'action':'create','payload':str(Path('created')/rel)}); continue
            if not ap.exists(): entries.append({'relative_path':rel.as_posix(),'action':'delete'}); continue
            if bp.read_bytes()==ap.read_bytes():
                if include_unchanged: entries.append({'relative_path':rel.as_posix(),'action':'unchanged'})
                continue
            r=XmlDiffCompiler().compile_files(bp,ap); cfgrel=Path('configs')/rel.with_suffix(rel.suffix+'.config.yaml'); cfgp=out/cfgrel; cfgp.parent.mkdir(parents=True,exist_ok=True); dump_one(r.config,cfgp)
            ok=r.verified
            if verify:
                extra = r.config
                if extra.get('xml_action') == 'replace_entire_file':
                    actual = str(extra.get('xml_exact_text', ''))
                else:
                    actual = XmlPatchEngine().apply_text(bp.read_text(encoding='utf-8-sig'), r.config)[0]
                ok=XmlDiffCompiler._structural_equal(actual,ap.read_text(encoding='utf-8-sig'))
            all_ok &= ok
            entries.append({'relative_path':rel.as_posix(),'action':'patch','config':cfgrel.as_posix(),'verified':ok,'strategy':r.strategy,'warnings':r.warnings})
        manifest={'version':1,'kind':'xml-folder-manifest','before_root':str(b),'after_root':str(a),'entries':entries,'verified':all_ok,
                  'counts':{k:sum(1 for e in entries if e['action']==k) for k in ('patch','create','delete','unchanged')}}
        mp=out/'manifest.yaml'; dump_one(manifest,mp); return XmlFolderCompileResult(mp,entries,all_ok)

    def apply_folder(self,source_root,generated_root,output_root,variables=None):
        src=Path(source_root).resolve(); gen=Path(generated_root).resolve(); out=Path(output_root).resolve(); manifest=load_one(gen/'manifest.yaml')
        if out.exists(): shutil.rmtree(out)
        shutil.copytree(src,out); report=[]
        for e in manifest['entries']:
            target=out/e['relative_path']; action=e['action']
            if action=='patch': XmlPatchEngine().apply_file(target,gen/e['config'],target,variables or {})
            elif action=='create': target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(gen/e['payload'],target)
            elif action=='delete' and target.exists(): target.unlink()
            report.append({'relative_path':e['relative_path'],'action':action})
        return {'version':1,'kind':'xml-folder-apply','output_root':str(out),'files':report,'counts':manifest.get('counts',{})}

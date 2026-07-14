from __future__ import annotations

from pathlib import Path
import fnmatch
import shutil
import tempfile
from typing import Any

from yaml_config_engine.models import EngineConfig
from yaml_config_engine.variable_scope import resolve_scope_variables
from yaml_config_engine.yamlio import load_one
from yaml_config_engine.config_loader import load_config_with_variable_maps

from .engine import XmlPatchEngine

XML_SUFFIXES = {'.xml'}


class XmlFolderEngine:
    def __init__(self) -> None:
        self.engine = XmlPatchEngine()

    @staticmethod
    def _filters(rule: dict[str, Any]) -> dict[str, list[str]]:
        raw = rule.get('filters') or {}
        aliases = {
            'path_allow': ('path_allow','path_whitelist','paths','include_paths'),
            'path_deny': ('path_deny','path_blacklist','exclude_paths'),
            'fab_allow_prefix': ('fab_allow_prefix','fab_whitelist','fab_allow'),
            'fab_deny_prefix': ('fab_deny_prefix','fab_blacklist','fab_deny'),
            'env_allow': ('env_allow','env_whitelist'),
            'env_deny': ('env_deny','env_blacklist'),
        }
        result = {}
        for canonical, names in aliases.items():
            value = next((raw.get(n) for n in names if n in raw), [])
            if isinstance(value, str): value = [value]
            result[canonical] = [str(x) for x in (value or [])]
        return result

    @staticmethod
    def _allowed(rel: Path, f: dict[str, list[str]]) -> bool:
        posix = rel.as_posix(); parts = rel.parts
        fab = parts[0] if len(parts) >= 3 else ''
        env = parts[1] if len(parts) >= 3 else ''
        app_rel = Path(*parts[2:]).as_posix() if len(parts) >= 3 else posix
        def match_any(patterns: list[str]) -> bool:
            return any(fnmatch.fnmatch(posix,p) or fnmatch.fnmatch(app_rel,p) or posix == p or app_rel == p for p in patterns)
        if f['path_allow'] and not match_any(f['path_allow']): return False
        if f['path_deny'] and match_any(f['path_deny']): return False
        if f['fab_allow_prefix'] and not any(fab.startswith(x) for x in f['fab_allow_prefix']): return False
        if f['fab_deny_prefix'] and any(fab.startswith(x) for x in f['fab_deny_prefix']): return False
        if f['env_allow'] and env not in f['env_allow']: return False
        if f['env_deny'] and env in f['env_deny']: return False
        return True

    def plan(self, source_root: str | Path, config: str | Path | dict[str, Any]) -> dict[str, Any]:
        source_root = Path(source_root).resolve()
        raw = load_config_with_variable_maps(config) if isinstance(config, (str,Path)) else config
        cfg = EngineConfig.model_validate(raw)
        ordered = sorted(enumerate(cfg.rules), key=lambda x:(-x[1].get('priority',0),x[0]))
        files=[]; counts={r['id']:0 for r in cfg.rules}
        for target in sorted(source_root.rglob('*.xml')):
            if not target.is_file(): continue
            rel=target.relative_to(source_root); matched=[]
            for _,rule in ordered:
                if not rule.get('enabled',True) or not self._allowed(rel,self._filters(rule)): continue
                matched.append(rule['id']); counts[rule['id']]+=1
                if rule.get('stop',False): break
            if matched or cfg.operations: files.append({'relative_path':rel.as_posix(),'rules':matched})
        return {'version':1,'kind':'xml-rules-plan','source_root':str(source_root),'files':files,
                'summary':{'matched_files':len(files),'rules':counts},'valid':True}

    def apply_rules(self, source_root: str | Path, config: str | Path | dict[str, Any], output_root: str | Path,
                    variables: dict[str,Any] | None=None) -> dict[str,Any]:
        source_root=Path(source_root).resolve(); final=Path(output_root).resolve()
        raw=load_config_with_variable_maps(config) if isinstance(config,(str,Path)) else config
        cfg=EngineConfig.model_validate(raw)
        final.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix=f'.{final.name}.stage-',dir=str(final.parent)))
        shutil.rmtree(stage); shutil.copytree(source_root,stage)
        ordered=sorted(enumerate(cfg.rules),key=lambda x:(-x[1].get('priority',0),x[0]))
        report=[]; cli_vars=dict(variables or {})
        for target in sorted(stage.rglob('*.xml')):
            if not target.is_file(): continue
            rel=target.relative_to(stage); parts=rel.parts
            fab=parts[0] if len(parts)>=3 else ''; env=parts[1] if len(parts)>=3 else ''
            app_rel=Path(*parts[2:]).as_posix() if len(parts)>=3 else rel.as_posix()
            file_ctx={'FAB':fab,'ENV':env,'PATH':rel.as_posix(),'RELATIVE_PATH':rel.as_posix(),
                      'APP_PATH':app_rel,'FILE_NAME':target.name,'FILE_STEM':target.stem}
            scope_vars, scopes=resolve_scope_variables(cfg.variable_map,fab,env)
            changed=False; applied=[]
            if cfg.operations:
                base={'version':cfg.version,'variables':cfg.variables,'options':cfg.options,
                      'defaults':cfg.defaults,'operations':cfg.operations}
                r=self.engine.apply_file(target,base,target,{**scope_vars,**cli_vars,**file_ctx})
                changed |= r.changed; applied.append('global')
            for _,rule in ordered:
                if not rule.get('enabled',True) or not self._allowed(rel,self._filters(rule)): continue
                rv,rs=resolve_scope_variables(rule.get('variable_map',{}),fab,env)
                rcfg={'version':cfg.version,'variables':{**cfg.variables,**scope_vars,**rv,**rule.get('variables',{})},
                      'options':{**cfg.options,**rule.get('options',{})},
                      'defaults':{**cfg.defaults,**rule.get('defaults',{})},'operations':rule['operations']}
                r=self.engine.apply_file(target,rcfg,target,{**cli_vars,**file_ctx})
                changed |= r.changed; applied.append(rule['id']); scopes += [f"{rule['id']}:{x}" for x in rs]
                if rule.get('stop',False): break
            if applied: report.append({'relative_path':rel.as_posix(),'changed':changed,'rules':applied,'variable_scopes':scopes})
        if final.exists(): shutil.rmtree(final)
        stage.replace(final)
        return {'version':1,'kind':'xml-rules-apply','output_root':str(final),'files':report,
                'summary':{'processed':len(report),'changed':sum(1 for x in report if x['changed'])}}

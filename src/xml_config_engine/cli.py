from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import xml.etree.ElementTree as ET

from yaml_config_engine.yamlio import dump_one, load_one
from yaml_config_engine.linting import ConfigLinter

from .engine import XmlPatchEngine
from .folder import XmlFolderEngine
from .compiler import XmlDiffCompiler
from .folder_compiler import XmlFolderCompiler


def parse_vars(items: list[str]) -> dict[str,str]:
    out={}
    for item in items:
        if '=' not in item: raise SystemExit(f'Invalid --var: {item}; expected NAME=VALUE')
        k,v=item.split('=',1); out[k]=v
    return out


def main(argv: list[str] | None=None) -> int:
    p=argparse.ArgumentParser(prog='xml-config-tool',description='Format-preserving XML config tool')
    sub=p.add_subparsers(dest='cmd',required=True)
    a=sub.add_parser('apply'); a.add_argument('source'); a.add_argument('config'); a.add_argument('-o','--output'); a.add_argument('--var',action='append',default=[]); a.add_argument('--variable-map-file',action='append',default=[]); a.add_argument('--dry-run',action='store_true')
    c=sub.add_parser('compile'); c.add_argument('before'); c.add_argument('after'); c.add_argument('-o','--output',required=True)
    cf=sub.add_parser('compile-folder'); cf.add_argument('before_root'); cf.add_argument('after_root'); cf.add_argument('output_root'); cf.add_argument('--include-unchanged',action='store_true'); cf.add_argument('--no-verify',action='store_true'); cf.add_argument('--layout',choices=['compact','expanded'],default='compact'); cf.add_argument('--matched-files-only',action='store_true')
    af=sub.add_parser('apply-folder'); af.add_argument('source_root'); af.add_argument('generated_root'); af.add_argument('output_root'); af.add_argument('--var',action='append',default=[]); af.add_argument('--variable-map-file',action='append',default=[])
    vf=sub.add_parser('verify-folder'); vf.add_argument('source_root'); vf.add_argument('generated_root'); vf.add_argument('expected_root')
    idem=sub.add_parser('check-idempotency'); idem.add_argument('source_root'); idem.add_argument('config'); idem.add_argument('--var',action='append',default=[])
    vc=sub.add_parser('validate-config'); vc.add_argument('config')
    cap=sub.add_parser('capabilities')
    v=sub.add_parser('verify'); v.add_argument('before'); v.add_argument('config'); v.add_argument('expected'); v.add_argument('--exact-text',action='store_true',default=True)
    ar=sub.add_parser('apply-rules-folder'); ar.add_argument('source_root'); ar.add_argument('config'); ar.add_argument('output_root'); ar.add_argument('--var',action='append',default=[])
    pr=sub.add_parser('plan-rules-folder'); pr.add_argument('source_root'); pr.add_argument('config')
    run=sub.add_parser('run-folder'); run.add_argument('source_root'); run.add_argument('config'); run.add_argument('output_root'); run.add_argument('--var',action='append',default=[]); run.add_argument('--allow-warnings',action='store_true')
    lint=sub.add_parser('lint'); lint.add_argument('config'); lint.add_argument('--source-root'); lint.add_argument('--var',action='append',default=[])
    args=p.parse_args(argv)
    engine=XmlPatchEngine()
    if args.cmd=='apply':
        r=engine.apply_file(args.source,args.config,args.output,parse_vars(args.var),dry_run=args.dry_run,variable_map_files=args.variable_map_file)
        print(json.dumps({'changed':r.changed,'output':str(r.output_path) if r.output_path else None,'skipped_operations':r.skipped_operations},ensure_ascii=False)); return 0
    if args.cmd=='compile':
        r=XmlDiffCompiler().compile_files(args.before,args.after); dump_one(r.config,args.output)
        print(json.dumps({'verified':r.verified,'structural_verified':r.verified,'strategy':r.strategy,'output':args.output,'warnings':r.warnings},ensure_ascii=False)); return 0 if r.verified else 2
    if args.cmd=='compile-folder':
        r=XmlFolderCompiler().compile_folder(args.before_root,args.after_root,args.output_root,args.include_unchanged,not args.no_verify,args.layout,args.matched_files_only)
        print(json.dumps({'verified':r.verified,'patch':str(r.compact_path) if r.compact_path else None,'primary':str(r.manifest_path),'counts':{k:sum(1 for e in r.entries if e['action']==k) for k in ('patch','create','delete','unchanged')}},ensure_ascii=False)); return 0 if r.verified else 2
    if args.cmd=='apply-folder':
        r=XmlFolderCompiler().apply_folder(args.source_root,args.generated_root,args.output_root,parse_vars(args.var),args.variable_map_file); print(json.dumps(r,ensure_ascii=False)); return 0
    if args.cmd=='verify-folder':
        with TemporaryDirectory(prefix='xml-folder-verify-') as tmp:
            actual=Path(tmp)/'actual'; XmlFolderCompiler().apply_folder(args.source_root,args.generated_root,actual)
            def snap(root): return {p.relative_to(root):p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}
            ok=snap(actual)==snap(Path(args.expected_root))
        print(json.dumps({'verified':ok,'exact_tree_verified':ok},ensure_ascii=False)); return 0 if ok else 2
    if args.cmd=='validate-config':
        cfg=engine.load_config(args.config); print(json.dumps({'valid':True,'operations':len(cfg.operations),'rules':len(cfg.rules),'rule_operations':sum(len(r.get('operations',[])) for r in cfg.rules)},ensure_ascii=False)); return 0
    if args.cmd=='capabilities':
        import sys; sys.path.insert(0,str(Path(__file__).resolve().parents[1])); from capabilities import report
        print(json.dumps(report(),ensure_ascii=False)); return 0
    if args.cmd=='verify':
        cfg=engine.load_config(args.config)
        extra=cfg.model_extra or {}
        if extra.get('xml_action')=='replace_entire_file': actual=extra.get('xml_exact_text','')
        else: actual=engine.apply_text(Path(args.before).read_text(encoding='utf-8-sig'),cfg)[0]
        expected=Path(args.expected).read_text(encoding='utf-8-sig')
        ok=actual==expected
        print(json.dumps({'verified':ok,'exact_text_verified':ok},ensure_ascii=False)); return 0 if ok else 2
    if args.cmd=='plan-rules-folder':
        print(json.dumps(XmlFolderEngine().plan(args.source_root,args.config),ensure_ascii=False)); return 0
    if args.cmd=='apply-rules-folder':
        r=XmlFolderEngine().apply_rules(args.source_root,args.config,args.output_root,parse_vars(args.var)); print(json.dumps(r,ensure_ascii=False)); return 0
    if args.cmd=='check-idempotency':
        with TemporaryDirectory(prefix='xml-idem-') as tmp:
            first=Path(tmp)/'first'; second=Path(tmp)/'second'
            vars_=parse_vars(args.var)
            XmlFolderEngine().apply_rules(args.source_root,args.config,first,vars_)
            XmlFolderEngine().apply_rules(first,args.config,second,vars_)
            def snap(root): return {p.relative_to(root):p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}
            ok=snap(first)==snap(second)
        print(json.dumps({'idempotent':ok},ensure_ascii=False)); return 0 if ok else 2
    if args.cmd=='lint':
        report=ConfigLinter().lint(args.config, extra_variables=parse_vars(args.var))
        payload=report.to_dict(); payload['format']='xml'; payload['note']='XML paths use XPath subset; runtime validates exact matches.'
        print(json.dumps(payload,ensure_ascii=False)); return 0 if report.valid else 2
    if args.cmd=='run-folder':
        cli_vars=parse_vars(args.var)
        report=ConfigLinter().lint(args.config, extra_variables=cli_vars)
        if not report.valid or (report.warnings and not args.allow_warnings):
            print(json.dumps({'stage':'lint','ok':False,'lint':report.to_dict()},ensure_ascii=False)); return 2
        result=XmlFolderEngine().apply_rules(args.source_root,args.config,args.output_root,cli_vars)
        # Strict syntax check and idempotency on produced XML.
        for f in Path(args.output_root).rglob('*.xml'): ET.fromstring(f.read_text(encoding='utf-8-sig'))
        with TemporaryDirectory(prefix='xml-idempotency-') as tmp:
            second=Path(tmp)/'out'; XmlFolderEngine().apply_rules(args.output_root,args.config,second,cli_vars)
            first_files={p.relative_to(args.output_root):p.read_bytes() for p in Path(args.output_root).rglob('*') if p.is_file()}
            second_files={p.relative_to(second):p.read_bytes() for p in second.rglob('*') if p.is_file()}
            idem=first_files==second_files
        print(json.dumps({'stage':'complete','ok':idem,'idempotent':idem,'result':result},ensure_ascii=False)); return 0 if idem else 2
    return 2

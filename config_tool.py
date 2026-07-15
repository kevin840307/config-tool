from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from mixed_folder import MixedFolderCompiler


def parse_vars(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        if '=' not in item:
            raise SystemExit(f'Invalid --var: {item}; expected NAME=VALUE')
        key, value = item.split('=', 1)
        out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='config-tool', description='Unified YAML + XML folder config tool')
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('compile-folder', help='Generate one patch.yaml for YAML and XML together')
    c.add_argument('before_root'); c.add_argument('after_root'); c.add_argument('output_root')
    c.add_argument('--include-unchanged', action='store_true'); c.add_argument('--no-verify', action='store_true')
    c.add_argument('--layout', choices=['compact','expanded'], default='compact')
    c.add_argument('--matched-files-only', action='store_true', help='Generate auto config only for paths present in both before and after')
    c.add_argument('--exact-bytes', action='store_true', help='Require byte-identical output; may use readable text or Base64 fallback')
    a = sub.add_parser('apply-folder', help='Apply one mixed YAML/XML patch.yaml')
    a.add_argument('source_root'); a.add_argument('generated_root'); a.add_argument('output_root')
    a.add_argument('--var', action='append', default=[])
    a.add_argument('--variable-map-file', action='append', default=[], help='Runtime variable mapping YAML; repeatable, later files override earlier files and patch mappings')
    v = sub.add_parser('verify-folder', help='Apply and byte-compare the whole mixed folder')
    v.add_argument('source_root'); v.add_argument('generated_root'); v.add_argument('expected_root')
    v.add_argument('--exact-bytes', action='store_true', help='Compare original bytes instead of YAML/XML structure')
    args = p.parse_args(argv)
    engine = MixedFolderCompiler()
    if args.cmd == 'compile-folder':
        result = engine.compile_folder(args.before_root, args.after_root, args.output_root,
                                       include_unchanged=args.include_unchanged,
                                       verify=not args.no_verify,
                                       layout=args.layout,
                                       matched_files_only=args.matched_files_only,
                                       exact_bytes=args.exact_bytes)
        print(json.dumps(result, ensure_ascii=False)); return 0 if result['verified'] else 2
    if args.cmd == 'apply-folder':
        result = engine.apply_folder(args.source_root, args.generated_root, args.output_root, parse_vars(args.var), args.variable_map_file)
        print(json.dumps(result, ensure_ascii=False)); return 0
    ok = engine.verify_folder(args.source_root, args.generated_root, args.expected_root, exact_bytes=args.exact_bytes)
    print(json.dumps({'verified': ok, 'verification_mode': 'exact-bytes' if args.exact_bytes else 'structural'}, ensure_ascii=False)); return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())

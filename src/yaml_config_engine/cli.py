from __future__ import annotations
import argparse, json, shutil
from tempfile import TemporaryDirectory
from pathlib import Path
from .engine import YamlPatchEngine
from .diff_compiler import DiffCompiler
from .folder_compiler import FolderCompiler
from .yamlio import load_one, load_all, dump_one
from .comparison import strict_equal, strict_documents_equal, strict_compare
from .linting import ConfigLinter
from .config_loader import _normalize_map_document, _merge_maps
from .variable_scope import resolve_scope_variables
from .mapping_generalizer import verified_generalize_config


def parse_vars(items: list[str]) -> dict[str, str]:
    result = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --var: {item}; expected NAME=VALUE")
        k, v = item.split("=", 1)
        result[k] = v
    return result


def parse_identity(items: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --identity: {item}; expected PATH=key1,key2")
        path, keys = item.split("=", 1)
        result[path] = [k.strip() for k in keys.split(",") if k.strip()]
    return result


def add_patterns(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--include", action="append", default=[], help="YAML file glob; repeatable")
    parser.add_argument("--exclude", action="append", default=[], help="Excluded YAML file glob; repeatable")
    parser.add_argument("--path-allow", "--path-whitelist", action="append", default=[],
                        help="Allowed relative path/glob. May omit FAB/ENV prefix; repeatable")
    parser.add_argument("--path-deny", "--path-blacklist", action="append", default=[],
                        help="Denied relative path/glob. Deny takes precedence; repeatable")
    parser.add_argument("--fab-allow-prefix", "--fab-whitelist", action="append", default=[],
                        help="Allowed FAB starts-with prefix; repeatable")
    parser.add_argument("--fab-deny-prefix", "--fab-blacklist", action="append", default=[],
                        help="Denied FAB starts-with prefix; deny takes precedence; repeatable")
    parser.add_argument("--env-allow", "--env-whitelist", action="append", default=[],
                        help="Allowed ENV exact name; repeatable")
    parser.add_argument("--env-deny", "--env-blacklist", action="append", default=[],
                        help="Denied ENV exact name; deny takes precedence; repeatable")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="yaml-config-tool")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("apply", help="Apply one config to one YAML file")
    a.add_argument("source"); a.add_argument("config"); a.add_argument("-o", "--output")
    a.add_argument("--var", action="append", default=[]); a.add_argument("--variable-map-file", action="append", default=[]); a.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("compile", help="Generate config from one before/after YAML pair")
    c.add_argument("before"); c.add_argument("after"); c.add_argument("-o", "--output", required=True)
    c.add_argument("--identity", action="append", default=[], help="Array identity rule PATH=key1,key2")
    c.add_argument("--retry-protection", action="store_true", help="Generate duplicate/idempotency guards for repeated execution; disabled by default")
    c.add_argument("--variable-map-file", action="append", default=[], help="Generalize generated values using an existing mapping YAML; repeatable")
    c.add_argument("--fab", default="", help="FAB scope used to resolve mapping variables, e.g. FAB14-FZ1")
    c.add_argument("--env", default="", help="ENV scope used to resolve mapping variables, e.g. STAGING")

    v = sub.add_parser("verify", help="Verify one generated config")
    v.add_argument("before"); v.add_argument("config"); v.add_argument("expected")

    cf = sub.add_parser("compile-folder", help="Generate one patch.yaml by default; use --layout expanded for detailed artifacts")
    cf.add_argument("before_root"); cf.add_argument("after_root"); cf.add_argument("output_root")
    add_patterns(cf)
    cf.add_argument("--include-unchanged", action="store_true")
    cf.add_argument("--no-verify", action="store_true")
    cf.add_argument("--layout", choices=["compact", "expanded"], default="compact")
    cf.add_argument("--matched-files-only", action="store_true", help="Generate auto config only for relative paths present in both before and after")
    cf.add_argument("--exact-bytes", action="store_true", help="Require byte-identical output; may use Base64 fallback")
    cf.add_argument("--retry-protection", action="store_true", help="Generate duplicate/idempotency guards; disabled by default")

    af = sub.add_parser("apply-folder", help="Apply a generated folder manifest")
    af.add_argument("source_root"); af.add_argument("generated_root"); af.add_argument("output_root")
    af.add_argument("--var", action="append", default=[]); af.add_argument("--variable-map-file", action="append", default=[])

    rf = sub.add_parser("apply-rules-folder", help="Apply one multi-rule config to a folder tree")
    rf.add_argument("source_root"); rf.add_argument("config"); rf.add_argument("output_root")
    rf.add_argument("--var", action="append", default=[]); rf.add_argument("--variable-map-file", action="append", default=[])

    pf = sub.add_parser("plan-rules-folder", help="Preview matched files and rule conflicts without writing")
    pf.add_argument("source_root"); pf.add_argument("config")

    ir = sub.add_parser("check-idempotency", help="Apply folder rules twice and verify the second run is unchanged")
    ir.add_argument("source_root"); ir.add_argument("config"); ir.add_argument("--var", action="append", default=[]); ir.add_argument("--variable-map-file", action="append", default=[])

    vf = sub.add_parser("verify-folder", help="Verify folder configs by applying them in a temporary directory")
    vf.add_argument("source_root"); vf.add_argument("generated_root"); vf.add_argument("expected_root")

    vc = sub.add_parser("validate-config", help="Validate and normalize a config without changing files")
    vc.add_argument("config")

    lint = sub.add_parser("lint", help="Lint a config and show actionable errors/warnings")
    lint.add_argument("config")
    lint.add_argument("--source-root", help="Also check rule matches and conflicts against this folder")
    lint.add_argument("--var", action="append", default=[])

    run = sub.add_parser("run-folder", help="Safe one-command lint, plan, apply, strict parse and idempotency workflow")
    run.add_argument("source_root"); run.add_argument("config"); run.add_argument("output_root")
    run.add_argument("--var", action="append", default=[]); run.add_argument("--variable-map-file", action="append", default=[])
    run.add_argument("--allow-warnings", action="store_true", help="Do not fail because lint produced warnings")

    args = p.parse_args(argv)
    if args.cmd == "apply":
        r = YamlPatchEngine().apply_file(args.source, args.config, args.output, parse_vars(args.var), dry_run=args.dry_run, variable_map_files=args.variable_map_file)
        print(json.dumps({"changed": r.changed, "output": str(r.output_path) if r.output_path else None, "skipped_operations": r.skipped_operations}, ensure_ascii=False))
        return 0
    if args.cmd == "compile":
        before_docs, after_docs = load_all(args.before), load_all(args.after)
        if len(before_docs) == len(after_docs) == 1:
            result = DiffCompiler(parse_identity(args.identity), retry_protection=args.retry_protection, readable=True).compile(before_docs[0], after_docs[0])
            config, verified, strategy, warnings = result.config, result.verified, result.strategy, result.warnings
        else:
            config = {
                'version': 1,
                'options': {'atomic_write': True},
                'folder_action': 'replace_all_documents',
                'document_mode': 'all',
                'operations': [{'id': 'replace-documents', 'op': 'replace', 'path': '$', 'value': after_docs}],
            }
            verified, strategy, warnings = True, 'replace-all-documents', ['Multi-document input used strict replace-all-documents.']
        generalized = False
        if args.variable_map_file and len(before_docs) == len(after_docs) == 1:
            merged_map = {}
            output_path = Path(args.output).resolve()
            stored_refs = []
            for ref in args.variable_map_file:
                ref_path = Path(ref).expanduser().resolve()
                merged_map = _merge_maps(merged_map, _normalize_map_document(load_one(ref_path), ref_path))
                try:
                    stored_refs.append(str(ref_path.relative_to(output_path.parent)))
                except ValueError:
                    stored_refs.append(str(ref_path))
            resolved_vars, matched_scopes = resolve_scope_variables(merged_map, args.fab, args.env)
            if not resolved_vars:
                warnings.append('Mapping generalization requested but no variables matched the selected FAB/ENV scope.')
            else:
                candidate, accepted = verified_generalize_config(before_docs[0], after_docs[0], config, resolved_vars)
                if accepted:
                    config = candidate
                    config['variable_map_file'] = stored_refs[0] if len(stored_refs) == 1 else stored_refs
                    config['scope'] = {'fab': args.fab, 'env': args.env}
                    generalized = True
                    warnings.append('Generalized generated values with mapping scopes: ' + ', '.join(matched_scopes))
                else:
                    warnings.append('Mapping generalization failed strict replay and was rolled back.')
        dump_one(config, args.output)
        print(json.dumps({"verified": verified, "structural_verified": verified, "generalized": generalized, "output": args.output, "strategy": strategy, "warnings": warnings}, ensure_ascii=False))
        return 0 if verified else 2
    if args.cmd == "verify":
        with TemporaryDirectory(prefix='yaml-verify-') as tmp:
            actual_path = Path(tmp) / Path(args.before).name
            shutil.copy2(args.before, actual_path)
            YamlPatchEngine().apply_file(actual_path, args.config, actual_path)
            actual_docs, expected_docs = load_all(actual_path), load_all(args.expected)
            ok = strict_documents_equal(actual_docs, expected_docs)
            differences = []
            if not ok:
                if len(actual_docs) != len(expected_docs):
                    differences = [{'path': '$', 'kind': 'document_count', 'actual': len(actual_docs), 'expected': len(expected_docs)}]
                else:
                    for i, (a, e) in enumerate(zip(actual_docs, expected_docs)):
                        differences.extend(strict_compare(a, e, f'$doc[{i}]').differences)
            print(json.dumps({"verified": ok, "structural_verified": ok, "differences": differences[:20]}, ensure_ascii=False, default=str))
            return 0 if ok else 2
    if args.cmd == "compile-folder":
        compiler = FolderCompiler(retry_protection=args.retry_protection, readable=True)
        result = compiler.compile_folder(
            args.before_root,
            args.after_root,
            args.output_root,
            include=args.include or None,
            exclude=args.exclude or None,
            include_unchanged=args.include_unchanged,
            verify=not args.no_verify,
            path_allow=args.path_allow or None,
            path_deny=args.path_deny or None,
            fab_allow_prefix=args.fab_allow_prefix or None,
            fab_deny_prefix=args.fab_deny_prefix or None,
            env_allow=args.env_allow or None,
            env_deny=args.env_deny or None,
            layout=args.layout,
            matched_files_only=args.matched_files_only,
            exact_bytes=args.exact_bytes,
        )
        summary = {
            "verified": result.verified,
            "patch": str(result.compact_path) if result.compact_path else None,
            "manifest": str(result.manifest_path),
            "counts": {
                action: sum(1 for x in result.entries if x.action == action)
                for action in ("patch", "create", "delete", "unchanged")
            },
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if result.verified else 2
    if args.cmd == "validate-config":
        cfg = YamlPatchEngine().load_config(args.config)
        print(json.dumps({"valid": True, "operations": len(cfg.operations), "rules": len(cfg.rules), "rule_operations": sum(len(r.get("operations", [])) for r in cfg.rules)}, ensure_ascii=False))
        return 0
    if args.cmd == "lint":
        plan = FolderCompiler().plan_rules_config(args.source_root, args.config) if args.source_root else None
        report = ConfigLinter().lint(args.config, source_root=args.source_root, plan=plan, extra_variables=parse_vars(args.var))
        print(json.dumps(report.to_dict(), ensure_ascii=False))
        return 0 if report.valid else 2
    if args.cmd == "run-folder":
        compiler = FolderCompiler()
        plan = compiler.plan_rules_config(args.source_root, args.config)
        cli_vars = parse_vars(args.var)
        lint_report = ConfigLinter().lint(args.config, source_root=args.source_root, plan=plan, extra_variables=cli_vars)
        if not lint_report.valid or (lint_report.warnings and not args.allow_warnings):
            print(json.dumps({"completed": False, "stage": "lint", "lint": lint_report.to_dict(), "plan": plan}, ensure_ascii=False))
            return 2
        idem = compiler.check_rules_idempotency(args.source_root, args.config, variables=cli_vars)
        if not idem['idempotent']:
            print(json.dumps({"completed": False, "stage": "idempotency", "lint": lint_report.to_dict(), "plan": plan, "idempotency": idem}, ensure_ascii=False))
            return 2
        output = Path(args.output_root).resolve()
        with TemporaryDirectory(prefix='yaml-safe-run-') as tmp:
            candidate = Path(tmp) / 'candidate'
            apply_report = compiler.apply_rules_config(args.source_root, args.config, candidate, variables=cli_vars)
            # Strictly parse every YAML document before committing the output tree.
            checked = 0
            for f in candidate.rglob('*'):
                if f.is_file() and f.suffix.lower() in {'.yaml', '.yml'}:
                    load_all(f); checked += 1
            output.parent.mkdir(parents=True, exist_ok=True)
            backup = output.with_name(output.name + '.previous')
            if backup.exists(): shutil.rmtree(backup)
            if output.exists(): output.replace(backup)
            try:
                shutil.copytree(candidate, output)
            except Exception:
                if output.exists(): shutil.rmtree(output)
                if backup.exists(): backup.replace(output)
                raise
            if backup.exists(): shutil.rmtree(backup)
        summary = {"completed": True, "stage": "committed", "output": str(output), "strict_yaml_files_checked": checked, "lint": lint_report.to_dict(), "plan": plan['summary'], "idempotency": idem, "apply": apply_report['summary']}
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if args.cmd == "apply-folder":
        FolderCompiler().apply_manifest(args.source_root, args.generated_root, args.output_root, variables=parse_vars(args.var), variable_map_files=args.variable_map_file)
        print(json.dumps({"applied": True, "output": str(Path(args.output_root).resolve())}, ensure_ascii=False))
        return 0
    if args.cmd == "apply-rules-folder":
        report = FolderCompiler().apply_rules_config(args.source_root, args.config, args.output_root, variables=parse_vars(args.var))
        print(json.dumps({"applied": True, "output": str(Path(args.output_root).resolve()), "summary": report["summary"]}, ensure_ascii=False))
        return 0
    if args.cmd == "plan-rules-folder":
        report = FolderCompiler().plan_rules_config(args.source_root, args.config)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report['valid'] else 2
    if args.cmd == "check-idempotency":
        report = FolderCompiler().check_rules_idempotency(args.source_root, args.config, variables=parse_vars(args.var))
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report['idempotent'] else 2
    ok = FolderCompiler().verify_manifest(args.source_root, args.generated_root, args.expected_root)
    print(json.dumps({"verified": ok}, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

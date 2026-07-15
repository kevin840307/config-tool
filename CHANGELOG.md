# Changelog

## 0.10.0-rc1

Release Candidate focused on API/CLI freeze and repeatable release validation.

- Freezes the public `ConfigTool` facade methods and existing CLI command names.
- Adds `release_check.py` for compile, self-test, and CLI smoke validation.
- Adds `benchmark.py` with a repeatable large-YAML compile/apply/verify baseline.
- Keeps semantic folder patches as the default; Base64 remains fallback-only.
- Retains compatibility with legacy full operations and current readable aliases.
- No new operation, selector, mapping scope, or patch syntax was introduced.

## 0.9.10

- Release hardening, atomic recovery, legacy patch compatibility, and folder verify runtime mappings.

## 0.9.9

- Folder compact/expanded/matched-only stabilization and readable UTF-8 XML create payloads.

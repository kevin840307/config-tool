# v0.10.0-rc27

- Added enterprise YAML normal/error scenario matrices.
- Fixed YAML merge-anchor cloning and replay verification.
- Fixed missing pure-expression variables returning Jinja Undefined values.
- Added a complete Traditional Chinese Config guide with all major operations and examples.

## v0.10.0-rc26

- Improved Auto compile performance without changing generated Config output.
- Literal strings bypass Jinja compilation; actual templates use bounded compiled-template caches.
- Added immutable path-token parsing cache while preserving the public mutable-list API.
- Removed redundant replay deep copies when operations do not reference `original`.
- Added golden-output, template-context isolation, malformed-template, path-cache mutation, and original-snapshot regression tests.
- Enterprise 13,385-line fixture: compile 12.0s -> 8.6s on the same container; 8 operations unchanged.
- Same-FAB real fixture patch SHA-256 remains identical to rc25.

## v0.10.0-rc25

- Added replay-verified merging of adjacent compatible `update_item` operations on the same target.
- Added replay-verified removal of redundant `paths` entries already covered by broader selectors.
- Retained existing parent scalar-to-`merge` optimization and added final low-risk cleanup ordering.
- Added Auto Config quality summary to `log.txt` warnings.
- Added dedicated safe-simplification regression tests.
- No arbitrary operation reordering; all candidates still require full replay.

## v0.10.0-rc24

- Added opt-in `defaults_profile: concise-v1` to Auto-generated YAML/XML patches.
- Auto output omits repeated `replace_value` fields `count: 1` and `expect_replacements: 1`; the engine restores them recursively, including nested `item_operations`.
- Auto output may omit `on_multiple_matches: all` for simple unique `update_item` matches when replay proves the shorter form is equivalent.
- Legacy patches without `defaults_profile` keep the historical behavior (`replace_value` defaults to replace-all and no expected replacement count).
- Added compatibility and replay regression tests for concise and legacy defaults.

## v0.10.0-rc23

- Added bounded dependency-aware merging for identical `update_item` operations separated only by same-path `copy_item` preparation.
- Merged updates are relocated after the relevant copy barriers and accepted only after full replay verification.
- No general-purpose operation reordering was added.

# v0.10.0-rc23

- Added cross-FAB + ENV folder regression based on `ROOT/FAB/ENV` usage.
- Compiles only FABA/ENV before→after, then applies the same patch to one target FAB/ENV.
- Added a 2,584-line pure YAML Helm values correctness fixture plus Spring YAML/XML config files.
- File-key templates now support variables embedded inside path segments, such as `app-{{ config_version }}-config`.
- Auto mapping now generalizes YAML create/replace payloads, not only patch operations.
- `replace_value` version fragments such as `06→12` are promoted to `{{ old_config_version }}→{{ config_version }}` when mapping evidence is unique.
- Mapping token replacement now respects identifier boundaries, preventing values such as `stg` from corrupting `postgresql`.
- Compact patch files no longer contain `file_key_generalization` evidence; evidence is written to `log.txt`.
- Mixed-folder create/replace documents render runtime/external variables before writing.


- Added an enterprise Helm values regression generator covering multiple apps, environments, regions, FABs, workloads, containers, HPA, resources, ingress, affinity, secrets, volumes, observability, and deep list/dict nesting.
- The generated fixture exceeds 10,000 YAML lines and verifies common wildcard consolidation plus environment/app-specific residual preservation.
- The bounded final semantic outer checkpoint now runs for any meaningful operation set (8+ operations), not only inputs above the 500-operation large-mode threshold.
- This fixes complex 200-500 operation Helm configs that exhausted the normal optimizer budget before final outer path consolidation.

# v0.10.0-rc19

- Added a guaranteed final `paths` compression pass that runs even when the optional optimizer deadline is exhausted.
- Added typed wildcard output: mapping fan-out uses `*`; list fan-out uses `[*]`.
- Added multi-shape path-set compression, so hundreds of concrete paths can become a few wildcard patterns.
- Added a bounded final large outer checkpoint from the latest replay-verified state.
- Added an 8,976-line nested YAML regression fixture and full auto compile/replay test.

- Added compiler fast-replay mode for YAML candidate verification.
- Auto compile no longer clones and strict-compares the complete YAML document after every operation during optimizer replay.
- Normal apply behavior and per-operation no-effect diagnostics remain unchanged.
- Final strict replay verification remains enabled.
- Large synthetic replay benchmark with 600 operations improved from about 2.35s to about 0.90s per replay in the packaged environment.

# v0.10.0-rc16

- Added bounded large-mode optimization for more than 500 operations.
- Uses one compile-wide deadline and candidate budget.
- Optimizes inner update_item structure first, checkpoints the replay-verified result, then rebuilds outer path groups from that optimized state.
- Replaces recursive restart scans with bounded batch proposals and replay splitting.
- Keeps the last replay-verified config when time or replay limits are reached.


- Added exact semantic replay-result caching for YAML and XML auto optimizers.
- Removed duplicate full-document replay of identical candidate operation sets.
- Preserved mapping order, scalar type, operation order, strict replay, timeout and rollback behavior.
- No config syntax or generated-result behavior change.
- Full release check passes.

## v0.10.0-rc15 - Semantic update_item path merging

- Compare operation semantics recursively instead of using dict `repr()` ordering.
- Merge `update_item` outer paths such as `$/a/b/c/p1` and `$/a/b/c/p2` even when nested field order or explicit default options differ.
- Preserve wildcard-first, `paths`, union fallback, replay verification and bounded optimizer protections.
- Added regression coverage for semantic-equivalent nested `update_item` operations.

## v0.10.0-rc11 - Bounded auto optimization

- Added YAML/XML optimizer time budgets and replay-candidate limits.
- Added repeated-state detection and retained fixed-point round limits.
- Budget exhaustion keeps the last replay-verified config instead of failing compile or returning an unverified simplification.
- Added environment overrides: `CONFIG_TOOL_OPTIMIZATION_TIMEOUT_SECONDS` and `CONFIG_TOOL_OPTIMIZATION_MAX_CANDIDATES`.
- Added regression tests proving candidate-limit fallback remains verified.

## v0.10.0-rc10 - Multi-path operations and layered auto optimization

- Added `paths` as an explicit multi-target alternative to `path`.
- Every `paths` entry supports the same wildcard, list, union and match selectors as `path`.
- `path` and `paths` are mutually exclusive; empty/invalid `paths` fail validation.
- YAML execution expands selector entries, preserves order and de-duplicates concrete targets.
- XML execution supports multiple selector entries under one operation.
- Auto compile now optimizes one layer at a time: wildcard first, then readable `paths`, then exact union, otherwise original operations.
- Every layer is accepted only after complete replay verification.

## v0.10.0-rc9 - Readable auto-config optimization

- Auto-removes redundant update_item matches when every list item receives the same change.
- Emits concise `*/[*]` direct paths instead of repeated matched update_item blocks.
- Removes redundant `on_multiple_matches: all` from direct selector operations.
- Deduplicates identical generated operations before wildcard generalization.
- Keeps exact match unions when only a subset of items should change.

# v0.10.0-rc9

- Auto-generated folder `patch.yaml` no longer embeds resolved `variables` or `variable_map` values.
- Compile-time mappings are used only to generalize paths/values and to verify the generated patch.
- Apply/verify must receive runtime values through `--var`, `--variable-map-file`, or the Python API.
- Existing older patches that contain top-level `variables` or `variable_map` remain supported.

# Changelog

## v0.10.0-rc9

- Restored wildcard-first single-file Config simplification behavior.
- YAML selector safety is now decided by full replay before falling back to exact unions.
- Changed identical-operation merging from all-groups-at-once rollback to incremental verified fixed-point merging.
- Normalized default-equivalent operation fields such as `missing: skip`, `expect_matches: 1`, and `on_multiple_matches: error` before grouping.
- Preserved common operations while retaining different per-path residual operations.
- Added regression coverage for wildcard replay authority, common-plus-residual extraction, and default-equivalent operation merging.

## v0.10.0-rc5
- Restored wildcard-first XML path generalization.
- Identical sibling operations now try `*` before exact name unions.
- Exact `[p1,p2]` union remains the verified fallback when wildcard would affect unrelated siblings.
- Added regressions for full-sibling wildcard and partial-sibling union behavior.

## v0.10.0-rc5

- Added exact XML path-name unions such as `/root/abc/[p1,p2]`.
- Auto compiler now merges identical same-file operations when only path segments differ.
- Exact union is attempted before wildcard, preventing unrelated sibling nodes from being modified.
- Different operation semantics remain as separate residual operations.
- Added fixed-point optimization and strict full-text replay rollback.
- Added regression coverage for exact union and non-merge residual behavior.

## v0.10.0-rc3

- Auto `compile-folder` now parameterizes compact `files:` keys from explicit `--var` values or `--variable-map-file` mappings.
- Auto compiler merges identical per-file configs into safe `*` or recursive `**` patterns.
- Wildcard generation is accepted only when the source match set exactly equals the original concrete file set.
- Generalization is supported consistently by YAML-only, XML-only, mixed-folder CLI, and the Python API.
- Generated patches record `file_key_generalization` evidence and retain replay verification/rollback behavior.

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

## v0.10.0-rc2

- Compact folder patch `files:` keys now support `{{ variable }}` path templates.
- Compact folder patch `files:` keys now support `*`, `?`, `[]`, and recursive `**` wildcard matching.
- Added consistent support across YAML-only, XML-only, and mixed YAML/XML folder patches.
- Wildcards only target existing files; concrete rendered paths may still create files.
- Added path traversal protection and explicit overlapping-pattern conflict errors.

## v0.10.0-rc15
- Run a bounded second optimizer pass after readable shorthand conversion.
- Merge outer update_item paths that become semantically identical only after item_operations are normalized to set/remove shorthand.
- Preserve replay verification and optimizer safety budgets for the second pass.

## v0.10.0-rc23
- Added real same-FAB/same-ENV phase and version lifecycle folder fixture.
- FAB, environment and namespace remain unchanged; only old/current/new versions are mapping parameters.
- Added deep K8s dict/list structures and path/paths wildcard consolidation regression.
- Version directories are pre-created; the tool only transforms values.yaml, YAML and XML content.

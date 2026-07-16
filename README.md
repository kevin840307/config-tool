# YAML/XML Config Tool v0.10.0-rc27

以 before/after 自動產生可讀 patch，套用後以 replay 驗證值、型別、mapping/list 順序及 YAML quote style。

## 快速開始

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml
python yaml_config_tool.py apply before.yaml patch.yaml -o result.yaml
python yaml_config_tool.py verify before.yaml patch.yaml after.yaml
```

搭配既有 mapping 泛化：

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml \
  --variable-map-file system-a-fab-values.yaml \
  --fab FAB14-FZ1 --env STAGING
```

Folder 與 mixed YAML/XML：

```bash
python config_tool.py compile-folder before after generated
python config_tool.py apply-folder before generated output
python config_tool.py verify-folder before generated after
```

## Quote 預設行為

不需要額外設定：

- Auto compiler 跟隨 after 的 plain、single、double quote style。
- 修改既有節點時優先保留目標樣式。
- 新增節點時依 after 樣式產生最小必要 metadata。
- Mapping 泛化後會再次 replay，quote 不一致就回退。

人工指定：

```yaml
operations:
  - op: set
    path: $/app/version
    value: '{{ version }}'
    quote: single
```

可用值：`auto`、`preserve`、`plain`、`single`、`double`。

巢狀值：

```yaml
- op: merge
  path: $/app
  value:
    version: '{{ version }}'
    endpoint: '{{ endpoint }}'
  quote_styles:
    value.version: single
    value.endpoint: double
```

## Auto compiler 原則

- replay 必須 100% 還原。
- 能 `*` 就使用 `*`；完整 list item 行為使用 `[*]`；部分 sibling 才使用 union。
- 每輪只有在 operation、重複內容或可讀性確實改善時才接受。
- Retry 防護預設關閉；需要時加 `--retry-protection`。
- 所有 major operation 預設 `missing: skip`，可明確改為 `error` 或 `create`。

完整說明見 `docs/`。

建議閱讀：

- `docs/QUICK_START_zh-TW.md`：快速開始。
- `docs/ALL_CONFIG_GUIDE_zh-TW.md`：所有 Config 欄位、operation 與簡單範例。
- `docs/CONFIG_REFERENCE_zh-TW.md`：精簡參考。
- `docs/XML_USER_GUIDE_zh-TW.md`：XML 專用說明。



## v0.10.0-rc27 企業 YAML 穩定化

- 新增 7 組正常企業 YAML scenario matrix：App/Phase/Version、K8s workload、深層 config、特殊 key、scalar list、型別/順序、anchor/alias。
- 新增 9 組錯誤與邊界測試：重複 key、缺變數、match 不唯一、非法 selector/create、非法 regex、index 越界、容器型別錯誤、atomic rollback、scalar 型別。
- 修正 ruamel `deepcopy` 切斷 YAML merge anchor/alias 關係，造成 compiler fallback 或繼承欄位實體化。
- 修正純 expression 模板缺變數時回傳 `Undefined` 物件而未立即失敗。
- 新增 `docs/ALL_CONFIG_GUIDE_zh-TW.md` 完整 Config 手冊。

## v0.10.0-rc1 Folder 穩定化

- 修正 Python API 的 YAML compact/expanded folder mapping 傳遞。
- 修正 XML compact/expanded folder mapping 傳遞。
- XML-only compact 對 UTF-8 新檔預設使用可讀 `create_text`，不再使用 Base64。
- 完整回歸 compact/expanded、patch/create/delete、matched-files-only。
- 300 檔 mixed YAML/XML 壓力測試通過，預設產物無 Base64。
- `a→c`、`a1→c1` optimizer 與 replay 持續通過。


## Python API（v0.10.0-rc1）

```python
from config_tool_api import ConfigTool

result = ConfigTool().compile("a.yaml", "b.yaml", "patch.yaml")
assert result.verified

ConfigTool().apply("a.yaml", "patch.yaml", "result.yaml")
assert ConfigTool().verify("a.yaml", "patch.yaml", "b.yaml").verified
```

同一 facade 支援 YAML、XML、mapping 泛化與 mixed folder。完整說明見 `docs/PYTHON_API_zh-TW.md`。

## v0.10.0-rc1 穩定化與自我檢查

v0.9.x 進入補齊與修 bug 階段。正式 Python API 現在會：

- 接受單一 mapping 路徑或多個 mapping 路徑。
- 自動建立 compile/apply 的輸出父資料夾。
- 在 before/after 或 before/expected 格式不一致時立即報錯。
- 保持 YAML、XML、mixed folder 的統一結果介面。

執行內建回歸測試：

```bash
python self_test.py
```

Windows：

```bat
python self_test.py
```

成功時最後顯示 `SELF-TEST PASS`。

## v0.10.0-rc1 Release Hardening

此版本不新增 action。主要補齊舊 patch 相容、folder verify runtime mapping、缺變數錯誤與 atomic recovery。

```python
result = tool.verify_folder(
    "source",
    "generated",
    "expected",
    format="yaml",
    variable_map_files="mapping.yaml",
)
assert result.verified
```

## Release Candidate 驗收

```bash
python release_check.py
python benchmark.py
```

`release_check.py` 會執行 Python 語法編譯、完整 self-test，以及 YAML/XML/mixed CLI smoke；`benchmark.py` 會建立大型 YAML 並執行 compile/apply/verify，輸出可比較的 JSON 基準。

公開 Python API 與 CLI 命令在 v0.10.0-rc1 起凍結；RC 階段只接受相容性 bug fix。

### Dynamic folder file keys

Compact folder patches support both path templates and glob matching:

```yaml
files:
  "{{ version }}/application.yaml":
    ops:
      - set: [$/version, "{{ version }}"]
  "*/logging.yaml":
    ops:
      - set: [$/level, INFO]
  "**/feature.xml":
    format: xml
    config:
      version: 1
      operations: []
```

Supported glob syntax: `*`, `?`, `[]`, and recursive `**`. Wildcards patch existing files only. Concrete rendered paths may create files. Overlapping patterns and unsafe absolute/`../` paths are rejected.



## Auto optimizer safety limits

Auto-generated config optimization is bounded and fail-safe. YAML and XML optimizers stop when any of these conditions is reached:

- fixed-point round limit;
- repeated-state detection;
- replay-candidate limit;
- per-document optimization time budget.

The last replay-verified config is retained; optimization timeout never replaces a valid partial result with an unverified candidate. Defaults can be overridden for unusually large files:

```text
CONFIG_TOOL_OPTIMIZATION_TIMEOUT_SECONDS=5
CONFIG_TOOL_OPTIMIZATION_MAX_CANDIDATES=2000
```

These limits apply only to optional config simplification, not to the final compile replay verification.

## v0.10.0-rc10 Optimizer Regression Restoration

The XML auto compiler now compacts identical operations inside the same file when only the path differs.

```yaml
operations:
  - op: set
    path: /root/abc/[p1,p2]
    value: true
    on_multiple_matches: all
```

Rules:

- Exact name union such as `/abc/[p1,p2]` is preferred when `*` would include unrelated children.
- Wildcard remains available when replay proves it is exact and safe.
- Operations are grouped only when every non-path field is identical.
- Different values, missing policies, replacement rules, selectors, or expectations remain separate.
- Every optimized candidate must reproduce the complete target XML text; otherwise it is rolled back.

## v0.10.0-rc3 Auto File-Key Generalization

`compile-folder` can now safely generate parameterized file keys.

```bash
python yaml_config_tool.py compile-folder before after generated --var version=v512
```

May produce:

```yaml
files:
  "{{ version }}/application.yaml":
    config: ...
```

When multiple existing files have identical generated configs, the compiler may merge them:

```yaml
files:
  "*/application.yaml":
    config: ...
```

Recursive `**` is used only when its exact source match set equals the concrete files being merged. Use `--variable-map-file`, plus optional `--fab` and `--env`, to derive path variables from an existing FAB/ENV mapping. Every generalized compact patch is replay-verified; unsafe candidates remain as concrete paths.

## External variable mapping in generated folder patches

Auto `compile-folder` may use a variable map to generalize values or file keys such as `{{ version }}/application.yaml`, but generated `patch.yaml` does not embed resolved parameter values. Supply the same mapping at apply/verify time:

```bash
python config_tool.py apply-folder before generated output --variable-map-file variable-map.yaml
```

Older patches with embedded top-level `variables` or `variable_map` remain readable for backward compatibility.

## Multi-target `paths`

Use `path` for one selector and `paths` for several selectors that share the same operation. They are mutually exclusive.

```yaml
operations:
  - op: set
    paths:
      - $/app/p1/enabled
      - $/app/[p2,p3]/enabled
      - $/legacy/*/enabled
    value: true
```

Every entry supports the same matching syntax as `path`, including `*`, `[*]`, key unions and item selectors. Entries are evaluated in order. YAML concrete targets are de-duplicated before execution.

Auto compile simplifies in verified layers:

1. one wildcard `path`;
2. one readable `paths` operation;
3. one exact union path;
4. original independent operations.

Each layer must reproduce the complete target document or it is rolled back independently.

## Large nested YAML regression

The package includes `tests/fixtures/large-complex` (8,976 lines per YAML) and `tests/test_large_complex_auto.py`. The test verifies that 504 generated operations compact to two replay-verified operations and that final `paths` entries compress into typed wildcard patterns.

## Cross-FAB folder template regression

Run:

```bash
python tests/test_cross_fab_folder_template.py
```

The regression compiles one `FABA/STG` folder and applies the generated patch to one `FABB/PROD` folder using a different external variable map. It verifies versioned Spring config paths, YAML/XML apply behavior, FAB short/code values, namespace values, and config-version upgrades.


## rc22 same-FAB phase/version fixture

See `examples/same-fab-phase-version-real/`. The FAB, ENV and namespace remain unchanged. Only `old_version`, `current_version` and `new_version` are external mapping parameters. Version directories are pre-created; the tool transforms only `values.yaml`, YAML and XML content.


## Concise defaults profile

Auto-generated YAML/XML patches use `defaults_profile: concise-v1`. This lets the compiler omit repetitive fields such as `count: 1` and `expect_replacements: 1` from `replace_value` operations. Legacy patches without the profile retain their original defaults and behavior. Explicit non-default values are always preserved.

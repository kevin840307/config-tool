# YAML/XML Config Tool v0.10.0-rc1

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

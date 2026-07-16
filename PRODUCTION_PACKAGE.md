# Production Package v0.10.0-rc27

包含 YAML/XML CLI、mixed-folder runner、完整中文文件與 examples。

本版新增 YAML quote auto-preservation、manual quote/quote_styles、quote-aware strict replay，並保留 mapping 泛化、selector optimizer、retry protection、comments/order preservation。

## v0.10.0-rc1 Python Facade

新增 `config_tool_api.py`：

```python
from config_tool_api import ConfigTool
```

公開且統一的 API：

- `compile`
- `apply`
- `verify`
- `compile_folder`
- `apply_folder`
- `verify_folder`

所有方法回傳 `ConfigToolResult`，可用 `to_dict()` 轉為 JSON-friendly dict。

## v0.10.0-rc1 Folder Bug Fixes

1. YAML-only compact/expanded apply 現在都會把 `variable_map_files` 傳入單檔 engine。
2. XML-only compact/expanded apply 現在都會把 `variable_map_files` 傳入單檔 engine。
3. XML UTF-8 create payload 預設以可讀文字保存；只有非 UTF-8/binary 才使用 Base64。
4. Self-test 新增 folder layout、create/delete、matched-only、mapping parity 與 no-Base64 regression。


## v0.10.0-rc1 Stabilization

本版開始以 regression-first 方式維護 v0.9.x，功能新增須有對應 before/after、compile、apply、verify 測試。

修正：

- `ConfigTool(... variable_map_files="mapping.yaml")` 不再把字串當成字元序列。
- compile/apply 輸出到不存在的巢狀目錄時會自動建立父目錄。
- YAML/XML 輸入格式不一致會得到明確 `ValueError`。
- 新增無外部測試框架依賴的 `self_test.py`。

## v0.10.0-rc1 Release Hardening

- 舊版完整 patch 與可讀簡寫 patch 相容性測試。
- `verify_folder` 支援 runtime variables / mapping files。
- 缺變數與 atomic output recovery 測試。
- 此版本不新增 action 或 config 語法。

## RC 驗收指令

```bash
python release_check.py
python benchmark.py
```

產物：`release-check-report.json`、`benchmark-report.json`。這些報告為本機執行結果，不代表未實際執行的平台。


## Optimizer resource protection

Optional auto-config simplification is bounded by a five-second per-document budget, 2,000 replay candidates, fixed-point round limits, and repeated-state detection. On any limit, the compiler keeps the last replay-verified configuration and continues final verification.

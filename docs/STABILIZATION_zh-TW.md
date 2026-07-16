# v0.9.x 穩定化政策

## 原則

v0.9.x 以修 bug、補邊界情境、提升錯誤訊息與回歸覆蓋為主，不任意增加 operation。

每個修正至少驗證：

1. before/after auto compile。
2. apply 後 100% replay。
3. CLI 與 `ConfigTool` Python API。
4. YAML 值、型別、dict/list 順序與 quote style。
5. 若涉及 folder，必須驗證 mixed YAML/XML。

## 內建測試

```bash
python self_test.py
```

目前涵蓋：

- YAML compile/apply/verify
- 單一外部 mapping 路徑
- quote-only diff
- sibling `*`
- list item `[*]`
- missing 預設 skip
- multi-document YAML
- XML compile/apply/verify
- mixed folder
- CLI smoke
- format mismatch

## 回報 bug 建議資料

請提供：

- 修改前檔案
- 修改後檔案
- mapping（若有）
- auto 產生的 patch
- 執行指令或 Python 呼叫程式碼
- 預期結果與實際錯誤


## v0.10.0-rc1 已完成矩陣

- mixed compact / expanded
- YAML compact / expanded runtime mapping
- patch / create / delete
- matched-files-only
- UTF-8 XML create 不使用 Base64
- 300 檔 mixed folder compile / verify
- a→c、a1→c1 永久回歸

一般 auto config 不會因此增加欄位；以上均為內部契約與封裝穩定化。

## v0.10.0-rc1 Release Hardening

- `verify_folder()` 與 `apply_folder()` 對齊，支援 `variables` 與 `variable_map_files`。
- 永久驗證 v0.8 完整格式 patch 與 v0.9 可讀簡寫格式。
- 缺少模板變數時提供明確錯誤，且不覆蓋既有輸出。
- atomic failure regression：失敗時保留原輸出，不留下半成品。
- Public Python API 介面凍結：`compile/apply/verify/compile_folder/apply_folder/verify_folder`。

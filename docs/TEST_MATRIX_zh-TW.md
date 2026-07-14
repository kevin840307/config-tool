# 測試矩陣與驗收範圍

## 執行方式

```bat
python -m pip install -r requirements-dev.txt
python -m pytest -q
coverage run --source=src -m pytest -q
coverage report -m
```

目前交付包的完整測試：

```text
114 passed
`src/` 程式碼行覆蓋率：85%
主要 compiler / folder compiler / patch engine 約為 88%～94%
```

測試數量會隨新增案例增加；驗收應以「全部通過、無跳過失敗」為準。

## YAML operation

已涵蓋：

```text
set / replace / remove / merge
rename_key / insert_key / copy_key / move_key
append / prepend / insert / insert_at / insert_before / insert_after
update_item / upsert_item / remove_item / copy_item / move_item
capture / copy_node / move_node / copy_item_to_node
```

包含：

- root replace 與 `create_missing`
- dot path、JSON Pointer escaped key、正負 index
- merge 的 overwrite / keep_existing / delete_null / append / prepend / unique
- duplicate allow / skip / update / error
- 零筆、多筆、預期筆數與錯誤策略
- before/after/index/first/last/out-of-range 位置
- copy/move conflict
- multi-document selector
- anchor、alias、單/雙引號、註解、UTF-8 BOM
- LF/CRLF 與自訂縮排

## XML operation

已涵蓋：

```text
set / replace / remove / merge
rename_key / insert_key / copy_key / move_key
append / prepend / insert / insert_at / insert_before / insert_after
update_item / upsert_item / remove_item / copy_item / move_item
capture / copy_node / move_node / copy_item_to_node
```

包含：

- attribute 與 nested element 的 `create_missing`
- self-closing parent
- nested update/remove
- insert_before/after 依 match 的精確順序
- copy_item 的 set/remove/duplicate policy
- node/key 跨 parent move，避免文字 patch 重疊
- namespace prefix、CDATA、DOCTYPE、processing instruction、comment
- mixed content compiler exact fallback
- LF/CRLF 與未修改區段 byte preservation

## Folder 與 rules

已涵蓋：

- 全資料夾共用 operations
- 指定 child、多層 child、單一檔案的 `path_allow`
- `path_deny` 排除 archive/backup
- 多條 rule 累加、priority 與 stop
- FAB 前綴與 ENV scope
- `FAB14:STAGING`、`FAB14-FZ1:STAGING`
- 內嵌/外部/多份 variable map 與 CLI `--var`
- compile-folder 的 patch/create/delete/unchanged
- compact `patch.yaml` 與 expanded manifest 相容
- 大量多檔 child rules
- report 檔不會在第二次執行被當成業務 config
- apply → parse → idempotency

## 多版本升級 E2E

YAML 與 XML 均測試：

1. 刪除 deprecated 舊版本。
2. 先複製目前最新版建立新版本。
3. 覆寫新版本號與狀態。
4. 再修改保留舊版本的巢狀參數，並驗證新版未誤繼承。
5. 新增參數與新 section。
6. 依 FAB/ENV 取得不同值。
7. 指定 child 路徑才執行。
8. 保留來源註解與未修改格式。
9. duplicate policy 防止再次建立相同版本。
10. 第二次執行 byte-level 不再改變。
11. compile-folder → apply-folder → verify-folder 精確重現。

詳細 config 見 [多版本升級完整案例](MULTI_VERSION_UPGRADE_zh-TW.md)。

## CLI 與平台

已涵蓋：

- YAML/XML `run-folder --var`
- YAML/XML `check-idempotency --var`
- lint 能識別 CLI 才提供的變數
- Linux wrapper 實際 smoke test
- Windows `.cmd` 與直接 Python 入口共用同一套跨平台核心

測試環境無 Windows `cmd.exe` 時，無法在 Linux 容器內真正啟動 `.cmd`；Windows wrapper 保持為薄包裝，核心功能由相同 Python 測試覆蓋。正式 Windows 發佈可再於 Windows CI 執行同一套 `pytest`。

## 已修正的複雜情境問題

這輪測試實際發現並修正：

- XML `insert_before/insert_after` 未依 match 定位。
- XML `copy_item` 未完整套用版本覆寫、巢狀新增與防重複。
- XML move 操作來源/目的文字區段重疊。
- XML self-closing parent 插入處理。
- XML folder compiler exact fallback 驗證路徑錯誤。
- YAML 刪除舊版本時，視覺上屬於下一版本的註解可能被 ruamel 綁在前一項而一併刪除。
- YAML 產生的 report 在第二次冪等性檢查被誤視為業務 YAML。
- CLI `--var` 在 lint、run-folder 與 idempotency 間未完全一致。
- YAML UTF-8 BOM 未在所有寫入路徑保留。

## Missing policy 與大型 section selector

已驗證 YAML/XML：

- `missing: error / skip / create`。
- 舊 `create_missing: true` 相容與衝突檢查。
- 新增完整巢狀 dict/list section，而非僅單一 value。
- YAML `key / key_pattern`。
- YAML list 與 XML repeated item 的 `name / name_pattern`。
- XML direct child `name / name_pattern`。
- glob、忽略大小寫 glob、regex、忽略大小寫 regex。
- Pattern 多筆命中、零命中 skip、pattern create 拒絕。
- 指定 index 插入大型 item。
- 大型 section update/replace/remove 與未命中 byte-identical。

## Wildcard、index 與陣列值匹配

- YAML mapping `*`。
- YAML list `[*]`。
- YAML `[N]` 0-based 與負索引。
- YAML nested `mapping.* -> list[*] -> [N]` 組合。
- YAML wildcard 後建立大型 dict/list suffix。
- YAML wildcard 零命中 `error / skip / create rejected`。
- YAML `match` 的 nested path、glob、regex、數值條件與 all/any/not。
- XML element `*`。
- XML repeated element `[*]`。
- XML `[N]` XPath 1-based。
- XML attribute predicate `[@name='...']`。
- XML child-value predicate `[status='active']`。
- XML wildcard + predicate + numeric index + attribute create 複雜組合。

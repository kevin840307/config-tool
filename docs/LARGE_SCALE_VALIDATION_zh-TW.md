# 大型與複雜設定驗證報告

這份報告說明交付包實際執行的大型 YAML/XML 壓力案例。驗證不只比較解析後的值，也會檢查原始 bytes、註解數量與位置、key/list 順序、BOM、LF/CRLF，以及第二次執行是否完全不再變更。

## 驗證結論

```text
152 passed
src/ 行覆蓋率：85%
```

所有測試均無 skip 或 xfail。完整測試指令：

```bat
python -m pip install -r requirements-dev.txt
python -m pytest -q
coverage run --source=src -m pytest -q
coverage report -m
```

## 大型案例規模

| 案例 | 規模 | 主要驗證 |
|---|---:|---|
| YAML compiler 大型單檔 | 4,092 行、604 個註解、約 107 KB | copy/move/remove/rename、巢狀 section、新增 list/dict、結果逐 byte 等於 after |
| YAML 手寫複合 operations | 2,992 行、444 個原始註解 | wildcard、`[*]`、`[N]`、pattern、copy item、指定位置、註解複製與冪等性 |
| YAML multi-document | 8 documents、2,231 行、208 個註解 | document 重排、巢狀 section、新增 item、exact-byte fallback |
| YAML child rules + variable map | 1,548 行 | child path、FAB/ENV、外部 variable-map、wildcard、pattern、未命中檔 byte-identical |
| YAML BOM/CRLF compiler | 1,600+ 行 | UTF-8 BOM、CRLF、flow style、引號、新增註解與 section 精確重現 |
| YAML 多檔資料夾 | 32+ 個既有大型檔案 | patch/create/delete/unchanged、單一 `patch.yaml`、每個結果逐 byte 比對 |
| XML 手寫 surgical operations | 1,568 行、120 個 component | attribute、predicate、index、nested create、CDATA、namespace、未修改區段完全保留 |
| XML compiler 資料夾 | 1,968 行、140 個 component | BOM、CRLF、comment 精確位置、create/delete、exact fallback、逐 byte 比對 |

## YAML 4,000+ 行 compiler 驗證

流程：

```text
before YAML
→ compile-folder
→ generated/patch.yaml
→ apply-folder
→ 與 after YAML 逐 byte 比較
→ 再套用一次
→ bytes 不得改變
```

同一案例同時包含：

- 12 個 application section。
- 每個 application 8 個 service。
- 每個 service 5 個 route。
- 600 個以上的 section/list item/inline comments。
- 所有 application 新增大型 `runtime` section。
- 所有 service 新增巢狀 `metadata` dict/list。
- 修改每個 service 的指定 route index。
- 移動、刪除及複製 service item。
- 複製後修改 identity、version、timeout，並新增大型 feature section。
- mapping key rename 與指定位置插入。
- tail sentinel comment 必須留在原位置。

驗收不是只做結構相等，而是：

```python
actual_bytes == expected_bytes
```

## 手寫大型複合 config 驗證

單一 config 同時執行：

```text
wildcard set + missing:create
list[*] + [N]
name_pattern 多筆更新
copy_item + item_operations
insert_key + after_key
remove_item + missing:skip
大型 dict/list section 新增
```

註解驗證會區分：

- 原始註解必須全部保留。
- `copy_item` 所複製 section 內的註解應同步複製。
- 不可因新增 section 而遺失下一個 sibling 的前置註解。
- 第二次執行不得再新增相同 item 或移動註解。

## Compiler 的 exact-byte 安全機制

`compile-folder` 會先產生結構化 operations，並以真實來源檔執行一次。除了 YAML/XML 結構外，還會比較輸出與 after 的原始 bytes。

若 operations 無法同時重現以下內容：

- 新增或移動的註解。
- 特殊空白或引號。
- BOM。
- LF/CRLF。
- multi-document 排版。
- mixed content 或複雜 XML prolog。

compiler 會在同一份 `patch.yaml` 中改用明確的 exact-byte fallback，而不是回傳「結構正確但格式不同」的結果。

compact patch 內可能看到：

```yaml
replace_bytes_base64: ...
```

新增檔案則可能使用：

```yaml
create_bytes_base64: ...
```

這是工具自動產生的內部 payload，用來精確保存原始 bytes；不建議人工編輯。日常手寫 config 仍使用一般 operations。

## Child folder 與 variable-map 大型案例

大型 child rules 案例同時驗證：

```yaml
filters:
  path_allow: [app-a/**]
```

以及：

```yaml
variable_map_file: variable-map.yaml
```

scope 由一般到精確疊加：

```text
FAB14
→ FAB14:STAGING
→ FAB14-FZ1:STAGING
```

只有指定 child 被修改；同一資料夾中未命中的大型 YAML 必須保持逐 byte 不變。再次以輸出資料夾為來源執行同一 config，結果也必須逐 byte 不變。

## XML 大型驗證

XML 測試包含：

- `*`、`[*]`、XPath `[N]`。
- attribute predicate 與 child-value predicate。
- `missing:create` 在帶 predicate 的既有 parent 下新增最後一層 section。
- 大量 repeated elements 的 pattern 更新。
- attribute 順序與引號。
- CDATA、namespace prefix、XML declaration、DOCTYPE/processing instruction 既有案例。
- self-closing element 展開。
- UTF-8 BOM 與 CRLF。
- comment 位於相鄰 component 邊界的精確位置。
- 新增與刪除 XML 檔案的 exact bytes。

## 本輪壓力測試實際發現並修正

- 巢狀 list/dict 更新曾整段替換，可能遺失內部註解。
- 模板渲染曾把 ruamel round-trip 容器降成一般 dict/list。
- 新增 mapping section 時，視覺上屬於下一個 sibling 的註解可能遺失或位置錯誤。
- template literal 的引號曾被錯誤套到 variable-map 輸出值。
- 多文件 YAML 僅做結構 replace，無法保證原始註解與排版逐 byte 相同。
- XML 在 predicate parent 下無法建立缺少的最後一層 child。
- XML 新建巢狀 section 第一次與第二次執行的縮排可能漂移。
- XML compact create 曾可能失去 BOM 或 CRLF。
- YAML/XML compiler 的 fallback 現在都能保存 exact bytes。

## 仍應保留的安全界線

以下情況不應強行猜測：

- wildcard 本身零命中時自動猜要新增幾個 item。
- `name_pattern`/`key_pattern` 搭配 `missing:create` 時猜實際名稱。
- 無法判定 identity 的 repeated item 自動推斷 move/copy。
- 手寫 XML mixed content 的任意 DOM 重排。

遇到 compiler 無法安全拆解的情況會使用明確 fallback；手寫 config 遇到模糊 selector 則會報錯或依 `missing:skip` 跳過，不會靜默產生不確定結果。

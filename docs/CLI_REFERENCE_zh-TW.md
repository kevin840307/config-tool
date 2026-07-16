# CLI 指令參考

## 入口

```text
YAML: python yaml_config_tool.py <command> ...
XML : python xml_config_tool.py <command> ...
```

Windows 可改用 `RUN_WINDOWS.cmd` / `RUN_XML_WINDOWS.cmd`；Linux 可改用 `RUN_LINUX.sh` / `RUN_XML_LINUX.sh`。

## 主要指令

| 指令 | 用途 |
|---|---|
| `apply` | 套用單檔 config |
| `compile` | 由 before/after 單檔產生 config |
| `verify` | 驗證單檔結果 |
| `compile-folder` | 由 before/after 資料夾產生 compact patch 與 manifest |
| `apply-folder` | 套用 folder patch |
| `verify-folder` | 驗證 folder patch |
| `apply-rules-folder` | 對資料夾執行一份 rules config |
| `plan-rules-folder` | 預覽規則命中與衝突，不寫檔 |
| `check-idempotency` | 套用兩次並確認第二次不再改變 |
| `validate-config` | 驗證並正規化 config |
| `lint` | 檢查 config、變數與規則 |
| `run-folder` | lint、plan、idempotency、apply、parse 一次完成 |

XML 另有 `capabilities` 可輸出 YAML/XML 對齊能力。

### 輸出模式

預設只產生 `generated/patch.yaml`：

```bat
python yaml_config_tool.py compile-folder before after generated
```

需要舊式逐檔產物時：

```bat
python yaml_config_tool.py compile-folder before after generated --layout expanded
```

XML 的 `compile-folder` 也支援相同的 `--layout compact|expanded`。

## 單檔

```bat
python yaml_config_tool.py compile before.yaml after.yaml -o config.yaml
python yaml_config_tool.py apply before.yaml config.yaml -o result.yaml
python yaml_config_tool.py verify before.yaml config.yaml after.yaml
```

XML：

```bat
python xml_config_tool.py compile before.xml after.xml -o config.yaml
python xml_config_tool.py apply before.xml config.yaml -o result.xml
python xml_config_tool.py verify before.xml config.yaml after.xml
```

`apply` 可加：

```text
--var NAME=VALUE       可重複
--dry-run              不寫入輸出
```

YAML `compile` 可加：

```text
--identity PATH=key1,key2
```

用於指定陣列項目的穩定識別欄位。

## 資料夾 before / after

```bat
python yaml_config_tool.py compile-folder before after generated
python yaml_config_tool.py apply-folder before generated output
python yaml_config_tool.py verify-folder before generated after
```

YAML `compile-folder` 支援：

```text
--include GLOB
--exclude GLOB
--path-allow GLOB
--path-deny GLOB
--fab-allow-prefix PREFIX
--fab-deny-prefix PREFIX
--env-allow ENV
--env-deny ENV
--include-unchanged
--no-verify
```

上述選項可重複。Config 內的 rule filter 應使用 `path_allow` / `path_deny`，不是 CLI 的 `include` / `exclude` 名稱。

XML `compile-folder` 支援 `--include-unchanged`、`--no-verify`。

## Rules folder

```bat
python yaml_config_tool.py plan-rules-folder source config.yaml
python yaml_config_tool.py apply-rules-folder source config.yaml output --var ENV=STAGING
python yaml_config_tool.py check-idempotency source config.yaml --var ENV=STAGING
python yaml_config_tool.py run-folder source config.yaml output --var ENV=STAGING
```

XML 使用相同命令與 `--var`。

`run-folder` 預設 lint warning 會阻擋執行；已審查且接受警告時可加：

```text
--allow-warnings
```

## Config 檢查

```bat
python yaml_config_tool.py validate-config config.yaml
python yaml_config_tool.py lint config.yaml --source-root source --var HOST=test
```

XML：

```bat
python xml_config_tool.py validate-config config.yaml
python xml_config_tool.py lint config.yaml --source-root source --var HOST=test
python xml_config_tool.py capabilities
```

## 回傳碼

```text
0  成功
2  驗證、lint、冪等性或參數檢查未通過
其他  執行環境或未預期錯誤
```

## 執行時 mapping 檔

```bash
python config_tool.py apply-folder source generated output --variable-map-file mappings/system-a.yaml
python yaml_config_tool.py apply source.yaml config.yaml -o output.yaml --variable-map-file mappings/system-a.yaml
python xml_config_tool.py apply source.xml config.yaml -o output.xml --variable-map-file mappings/system-a.yaml
```

`--variable-map-file` 可重複指定。後面的 mapping 覆蓋前面的 mapping，也會覆蓋 patch 內相同 scope/變數；CLI `--var` 最後覆蓋全部。

## Auto config 可讀模式與 retry 防護

`compile` 與 `compile-folder` 預設產生較容易人工修改的簡寫 config，並且不加入 retry/duplicate 防護。所有簡化候選都必須 replay 後與 after 在值、型別、mapping/list 順序上完全一致，否則自動回退。

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml
```

需要重複執行防護時才加入：

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml --retry-protection
```

Folder 模式：

```bash
python yaml_config_tool.py compile-folder before after generated --retry-protection
```

未指定 `--retry-protection` 時，不會自動輸出 `duplicate`。指定後，compiler 會對可能重複新增的 list item 產生 identity 防護；第一次 replay 仍必須 100% 還原，第二次套用則不得新增重複資料。

## Compile 後依 mapping 自動泛化

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml \
  --variable-map-file mapping.yaml \
  --fab FAB14-FZ1 \
  --env STAGING
```

- 可重複指定 `--variable-map-file`，後面的 mapping 覆蓋前面的同名變數。
- 完整 scalar 相等時優先替換為 `{{ variable }}`。
- 字串可唯一組合時替換為模板，例如 `fab14-stg.com.tw` → `{{ fab_full }}{{ env }}.com.tw`。
- 每次泛化後均 strict replay；無法 100% 還原時自動回退。


# Compile quote 行為

`compile` 不需要 quote 參數；預設直接讀取 after 的 scalar style。搭配 `--variable-map-file` 時，變數化後仍驗證 quote style。

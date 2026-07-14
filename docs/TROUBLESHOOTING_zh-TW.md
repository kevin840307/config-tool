# 常見問題與排錯

## ModuleNotFoundError

```bat
python -m pip install -r requirements.txt
```

確認安裝與執行使用同一個 Python：

```bat
where python
python -m pip --version
```

Linux：

```bash
which python3
python3 -m pip --version
```

## variable_map_file 找不到

路徑以「引用它的 config 所在資料夾」為基準，不是命令列目前目錄。

```text
config/
├─ rules.yaml
└─ variables/
   └─ variable-map.yaml
```

```yaml
variable_map_file: variables/variable-map.yaml
```

## 變數沒有替換

檢查：

1. 模板是否寫成 `{{ NAME }}`。
2. 變數名稱大小寫是否一致。
3. FAB/ENV 是否由相對路徑正確解析。
4. rule 是否真的命中該檔案。
5. 外部 variable map 格式是否正確。
6. lint 時若變數只由 CLI 提供，也要帶相同 `--var`。

```bat
python yaml_config_tool.py lint config.yaml --source-root source --var NAME=value
```

## FAB14:STAGING 是否支援

支援。也支援 `FAB14-FZ1:STAGING`。較精確 scope 會在較寬 scope 後套用。

## Child rule 沒命中

Config rule 使用：

```yaml
filters:
  path_allow: ["child/**"]
  path_deny: ["child/archive/**"]
```

不要在 rule 內寫 CLI 專用名稱 `include` / `exclude`。路徑 pattern 一律用 `/`，即使在 Windows。

先執行：

```bat
python yaml_config_tool.py plan-rules-folder source config.yaml
```

## Rule 覆蓋順序不如預期

所有命中的 rule 會累加。`priority` 越高越早執行；後執行的 operation 可能覆蓋前值。同 priority 依宣告順序。使用 `plan-rules-folder` 查看實際順序，或用 `stop: true` 停止後續 rule。

## YAML 縮排沒有符合預期

```yaml
options:
  yaml_output:
    mapping: 2
    sequence: 4
    offset: 2
```

`offset` 必須小於 `sequence`。縮排設定影響被寫回文件的 YAML 序列化；註解、順序與引號由 round-trip 模式保留。

## LF / CRLF 不符合預期

YAML：

```yaml
options:
  yaml_output:
    line_ending: preserve  # preserve / lf / crlf
```

XML：

```yaml
options:
  xml_output:
    line_ending: preserve
```

`preserve` 是預設值。

## update_item 沒命中

建議明確設定：

```yaml
match: {name: api}
expect_matches: 1
```

零筆時預設失敗。預期可能不存在時改用 `upsert_item`，或對 remove 使用 `on_zero_matches: ignore`。

## copy_item 第二次重複建立

加入唯一欄位與 policy：

```yaml
duplicate:
  unique_by: [version]
  policy: skip
```

XML attribute 欄位寫成 `['@id']`。

## remove_item 後註解位置異常

工具會處理 ruamel 將「下一個 sequence item 前的獨立註解」掛在前一個 item 深層 key 的情況。若是 inline comment，它會視為被刪項目的一部分而一併刪除，這是預期行為。

## verify 失敗

先產生實際輸出再比較：

```bat
python yaml_config_tool.py apply before.yaml config.yaml -o actual.yaml
python yaml_config_tool.py verify before.yaml config.yaml expected.yaml
```

YAML 驗證會比較結構、型別與順序；XML `verify` 比較精確文字結果，folder verify 比較完整檔案樹 bytes。

## idempotency 失敗

常見原因：

- 每次無條件 append/prepend。
- copy_item 沒有 duplicate policy。
- match 不唯一。
- 模板包含每次變動的時間/隨機值。
- 第二次執行時輸出報告或非業務檔被規則命中。
- `check-idempotency` 沒帶與正式執行相同的 `--var`。

## XML path 命中多個節點

增加 `element`、attribute/子節點 match 與 `expect_matches`：

```yaml
- op: update_item
  path: /application/versions
  element: version
  match: {'@id': '2026.01'}
  expect_matches: 1
  set: {parameters.timeout: '45'}
```

## XML mixed content

`<message>Hello <b>Kevin</b>, welcome!</message>` 這類文字與子節點交錯內容不適合任意 DOM 重排。自動 compiler 無法安全拆分時會 exact fallback。手寫 operation 前應先在測試資料執行 `verify` 與 `check-idempotency`。

## Windows 可以、Linux 不行，或反過來

- Config path pattern 統一使用 `/`。
- 指令中的實體路徑可使用作業系統原生格式。
- 不要在 config 寫死磁碟機代號。
- 確認腳本有執行權限：`chmod +x RUN_LINUX.sh RUN_XML_LINUX.sh`。
- 換行使用 `preserve`，或依專案明確指定 `lf` / `crlf`。

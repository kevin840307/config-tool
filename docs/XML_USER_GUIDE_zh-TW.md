# XML 使用指南

XML 與 YAML 共用 config loader、變數系統、folder rules 與主要 CLI 流程，但 XML 由獨立的文字區段 engine 執行，不會改動 YAML engine。

## 設計原則

XML 不以整份 DOM 重新序列化為預設。安全可定位時只替換命中的最小文字區段，以保留：

- XML declaration
- DOCTYPE 與 processing instruction
- 註解與位置
- CDATA
- namespace prefix
- 屬性順序與單/雙引號
- 縮排、空白與 LF/CRLF
- 未修改節點的原始文字

Compiler 無法同時做到最小修改與精確重現時，才產生有標記的 exact fallback。

## 單檔與資料夾

```bat
python xml_config_tool.py compile before.xml after.xml -o config.yaml
python xml_config_tool.py apply before.xml config.yaml -o result.xml
python xml_config_tool.py verify before.xml config.yaml after.xml
```

```bat
python xml_config_tool.py compile-folder before after generated
python xml_config_tool.py apply-folder before generated output
python xml_config_tool.py verify-folder before generated after
```

## 基本設定

```yaml
version: 1
format: xml
options:
  xml_output:
    line_ending: preserve
operations:
  - op: replace
    path: /configuration/server/port
    value: "9090"
  - op: set
    path: /configuration/server/@enabled
    value: "true"
    create_missing: true
```

## 重複節點

來源：

```xml
<versions>
  <version id="2025.10" status="deprecated">
    <parameters><timeout>10</timeout></parameters>
  </version>
  <version id="2026.01" status="active">
    <parameters><timeout>20</timeout><retry>2</retry></parameters>
  </version>
</versions>
```

刪除舊版、先複製目前最新版，再更新保留舊版：

```yaml
operations:
  - op: remove_item
    path: /versions
    element: version
    match: {'@id': '2025.10'}
    on_zero_matches: ignore
    remove_leading_comments: true

  - op: copy_item
    path: /versions
    element: version
    source:
      match: {'@id': '2026.01'}
      expect_matches: 1
    set:
      '@id': '2026.07'
      '@status': candidate
      parameters.retry: "5"
      parameters.newParameter: enabled
      sections.featureFlags.enabled: "true"
    duplicate:
      unique_by: ['@id']
      policy: skip
    position:
      after:
        match: {'@id': '2026.01'}

  - op: update_item
    path: /versions
    element: version
    match: {'@id': '2026.01'}
    expect_matches: 1
    set:
      parameters.timeout: "45"
      parameters.compatibility: legacy-compatible
```

巢狀 `set` 路徑會只更新或建立必要的子節點/屬性；第二次執行可透過 duplicate policy 與 match 保持冪等。

## insert_before / insert_after

```yaml
- op: insert_after
  path: /application/services
  element: service
  match: {'@name': api}
  expect_matches: 1
  value: '<service name="worker" enabled="true"/>'
```

位置是依 match 的實際節點計算，不是固定追加。

## move_node / move_key

```yaml
- op: move_node
  from_path: /application/templates/template
  to_path: /application/active/template
```

XML engine 會先移除來源再重新解析目的區段，避免來源與目的文字範圍重疊造成錯誤。

## Namespace、CDATA 與 mixed content

工具可保留原始 namespace prefix、CDATA、DOCTYPE 與 processing instruction。需要修改 namespace 節點時，應使用檔案中可唯一定位的實際 path/attribute；複雜 mixed content，例如：

```xml
<message>Hello <b>Kevin</b>, welcome!</message>
```

若 compiler 無法安全拆成最小 operation，會採 exact fallback，而不是產生可能破壞文字順序的 config。

## Folder rules 與變數

```yaml
variable_map_file: variables/variable-map.yaml
rules:
  - id: staging-app
    filters:
      path_allow: ["FAB14-FZ1/STAGING/app/**"]
      fab_allow_prefix: [FAB14]
      env_allow: [STAGING]
    operations:
      - op: set
        path: /application/server/@host
        value: "{{ HOST }}"
        create_missing: true
```

```bat
python xml_config_tool.py run-folder source config.yaml output --var HOST=temporary
```

## 驗證建議

正式執行前依序使用：

```bat
python xml_config_tool.py lint config.yaml --source-root source
python xml_config_tool.py plan-rules-folder source config.yaml
python xml_config_tool.py check-idempotency source config.yaml
python xml_config_tool.py run-folder source config.yaml output
```

`run-folder` 會檢查輸出 XML 語法並再次套用確認冪等。

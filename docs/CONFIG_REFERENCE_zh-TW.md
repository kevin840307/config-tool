# Config 完整參考

## 基本結構

```yaml
version: 1
variables: {}
variable_map_file: variables/variable-map.yaml
variable_map: {}
options: {}
defaults: {}
documents: null
operations: []
rules: []
```

未使用的欄位可以省略。

## 變數

### 一般變數

```yaml
variables:
  PORT: 9090
operations:
  - op: set
    path: $.server.port
    value: "{{ PORT }}"
```

### 外部 variable map

```yaml
variable_map_file:
  - variables/common.yaml
  - variables/project.yaml
```

外部檔可包含 `variable_map:`：

```yaml
variable_map:
  FAB14:
    HOST: server-a
  FAB14:STAGING:
    HOST: server-a-staging
```

也可直接以 scope 為根：

```yaml
FAB14:
  HOST: server-a
```

支援：

```text
FAB14
FAB14:STAGING
FAB14-FZ1
FAB14-FZ1:STAGING
```

覆蓋順序由低到高：

```text
前方 variable_map_file
→ 後方 variable_map_file
→ 主 config 內嵌 variable_map
→ rule variable_map_file
→ rule 內嵌 variable_map
→ rule variables
→ CLI --var
```

可用內建變數包括 `FAB`、`ENV`、`PATH`、`RELATIVE_PATH`、`APP_PATH`、`FILE_NAME`、`FILE_STEM`。

## YAML 輸出參數

```yaml
options:
  yaml_output:
    mapping: 2
    sequence: 4
    offset: 2
    width: 4096
    preserve_quotes: true
    explicit_start: null
    explicit_end: null
    line_ending: preserve
```

| 參數 | 預設 | 說明 |
|---|---:|---|
| `mapping` | `2` | Mapping 巢狀縮排 |
| `sequence` | `4` | Sequence 總縮排 |
| `offset` | `2` | `-` 的縮排位移，必須小於 `sequence` |
| `width` | `4096` | 自動換行寬度 |
| `preserve_quotes` | `true` | 保留單/雙引號風格 |
| `explicit_start` | `null` | `true` 時輸出 `---` |
| `explicit_end` | `null` | `true` 時輸出 `...` |
| `line_ending` | `preserve` | `preserve` / `lf` / `crlf` |

也接受 `mapping_indent`、`sequence_indent`、`sequence_offset`、`line_width`。

XML 使用：

```yaml
options:
  xml_output:
    line_ending: preserve
```

## YAML path

支援根路徑 `$`、dot path、陣列索引與 JSON Pointer 形式。常用例子：

```text
$.server.port
$.services[0].name
/metadata/labels/app
```

有特殊字元的 key 建議使用 JSON Pointer escaping，例如 `/a~1b` 表示 key `a/b`。

## XML path

常用例子：

```text
/configuration/server/port
/configuration/appSettings/add/@value
/application/versions
```

重複節點應搭配 `element` 與 `match`，避免修改到錯誤節點。

## 基本 operations

### set / replace

兩者都會設定指定 path；`create_missing: true` 可建立缺少的路徑。

```yaml
- op: replace
  path: $.server.port
  value: 9090

- op: set
  path: $.new.section.enabled
  value: true
  create_missing: true
```

XML 屬性：

```yaml
- op: set
  path: /configuration/server/@enabled
  value: "true"
  create_missing: true
```

### remove

```yaml
- op: remove
  path: $.legacy
```

### merge

```yaml
- op: merge
  path: $.server
  strategy: overwrite
  value:
    host: localhost
    timeout: 30
```

YAML strategy：`overwrite`、`keep_existing`、`delete_null`；list 另支援 `append`、`prepend`、`unique`。

### rename_key

```yaml
- op: rename_key
  path: $.server
  old_key: port
  new_key: http_port
```

### insert_key

```yaml
- op: insert_key
  path: $.server
  key: timeout
  value: 30
  position:
    after_key: host
```

可用 `before_key`、`after_key`、`index`。

### copy_key / move_key

```yaml
- op: copy_key
  path: $.server
  source_key: host
  target_key: backup_host
  position: {after_key: host}
  on_conflict: error
```

`on_conflict` 可用 `error` 或覆寫策略（依 operation runtime 驗證）。

### copy_node / move_node

```yaml
- op: copy_node
  from_path: $.templates.latest
  to_path: $.versions.new
  create_missing: true
```

## Sequence / 重複節點 operations

### append / prepend

```yaml
- op: append
  path: $.services
  value: {name: worker, enabled: true}
```

### insert / insert_at

```yaml
- op: insert_at
  path: $.services
  index: 1
  value: {name: worker}
```

`position.index` 超出範圍時可用 `on_out_of_range: append` 或 `clamp`。

### insert_before / insert_after

```yaml
- op: insert_after
  path: $.services
  match: {name: api}
  expect_matches: 1
  value: {name: worker}
```

XML 加上重複節點名稱：

```yaml
- op: insert_after
  path: /application/versions
  element: version
  match: {'@id': '2026.01'}
  value: '<version id="2026.07"/>'
```

### update_item

更新已存在項目；沒有命中時預設失敗。

```yaml
- op: update_item
  path: $.versions
  match: {version: "2026.01"}
  expect_matches: 1
  set:
    parameters.timeout: 45
    parameters.compatibility: legacy-compatible
  remove:
    - parameters.obsolete
```

XML：

```yaml
- op: update_item
  path: /application/versions
  element: version
  match: {'@id': '2026.01'}
  set:
    '@status': active
    parameters.timeout: 45
    parameters.compatibility: legacy-compatible
  remove:
    - parameters.obsolete
```

### upsert_item

有命中時更新，沒有命中時新增：

```yaml
- op: upsert_item
  path: $.services
  match: {name: api}
  set: {enabled: true}
  value: {name: api, enabled: true}
  position: {last: true}
```

### remove_item

```yaml
- op: remove_item
  path: $.versions
  match: {version: "2025.10"}
  expect_matches: 1
  remove_leading_comments: true
```

可用 `on_zero_matches: ignore`；預設多筆命中會失敗，除非明確指定匹配策略。

### copy_item

適合「複製目前最新版建立新版本」：

```yaml
- op: copy_item
  path: $.versions
  source:
    match: {version: "2026.01"}
    expect_matches: 1
  set:
    version: "2026.07"
    status: candidate
    parameters.retry: 5
  remove:
    - parameters.temporary
  duplicate:
    unique_by: [version]
    policy: skip
  copy_leading_comments: false
  position:
    after:
      match: {version: "2026.01"}
      expect_matches: 1
```

`duplicate.policy`：`allow`、`skip`、`update`、`error`（依 operation 支援範圍）。YAML 複製版本時可用 `copy_leading_comments: false` 避免來源項目前置註解被複製；YAML/XML 刪除項目時可用 `remove_leading_comments: true` 一併移除緊鄰該項目的前置註解。這些選項預設維持既有相容行為。

YAML `copy_item` 還可使用 `item_operations` 對複製出的項目執行巢狀 operation：

```yaml
item_operations:
  - op: insert_key
    path: $.sections
    key: featureFlags
    value: {enabled: true}
```

### move_item

```yaml
- op: move_item
  path: $.services
  match: {name: worker}
  position: {first: true}
```

也可用 `source.match` / `source.index`。

### copy_item_to_node

從重複項目中選一筆，複製到另一個 node。YAML 的 `to_path` 是目的 mapping path；XML 的 `to_path` 是目的 parent element。

```yaml
- op: copy_item_to_node
  from_path: $.versions
  to_path: $.templates.latest
  source:
    match: {version: "2026.01"}
    expect_matches: 1
  set: {version: template}
```

XML：

```yaml
- op: copy_item_to_node
  from_path: /application/versions
  to_path: /application/templates
  element: version
  source:
    match: {'@id': '2026.01'}
    expect_matches: 1
  set: {'@id': template}
  duplicate:
    unique_by: ['@id']
    policy: skip
```

### capture

把來源 path 值存成變數供後續 operation 使用：

```yaml
- op: capture
  path: $.version
  name: OLD_VERSION
```

## Match 與位置

常用 match：

```yaml
match:
  name: api
  environment: STAGING
```

XML attribute 使用 `@`：

```yaml
match:
  '@id': '2026.01'
```

常用位置：

```yaml
position: {first: true}
position: {last: true}
position: {index: 2}
position: {before: {match: {name: api}, expect_matches: 1}}
position: {after: {match: {name: api}, expect_matches: 1}}
```

## Folder rules

```yaml
version: 1

operations:
  - op: set
    path: $.common.timeout
    value: 30
    create_missing: true

rules:
  - id: fab14-staging-child
    priority: 100
    filters:
      path_allow:
        - "child-a/**"
      path_deny:
        - "child-a/archive/**"
      fab_allow_prefix:
        - FAB14
      env_allow:
        - STAGING
    variable_map_file: variables/child-a.yaml
    operations:
      - op: replace
        path: $.server.host
        value: "{{ HOST }}"
```

Rule filter 支援：

```text
path_allow
path_deny
fab_allow_prefix
fab_deny_prefix
env_allow
env_deny
```

Deny 優先於 allow。路徑 pattern 一律使用 `/`，Windows/Linux 共用同一份 config。

所有符合的 rule 會累加執行。`priority` 數字越高越早執行；同 priority 依 config 宣告順序。因為後執行的操作可能覆蓋前值，請用 `plan-rules-folder` 確認順序。`stop: true` 可在該 rule 命中後停止後續 rule。

## Multi-document YAML

可使用 `documents` selector 限制只修改特定 document。由 before/after compiler 處理多 document 時，無法安全拆分的情況會採嚴格 replace-all-documents，確保結果精確。

## Compact folder patch

`compile-folder` 產生的 `patch.yaml` 會把相對路徑當成 key，記錄：

```text
patch       修改既有檔案
create      新增檔案
delete      刪除檔案
unchanged   未改變（通常只記摘要）
```

預設 generated 資料夾內只有 `patch.yaml`。`apply-folder` 可直接傳入 generated 資料夾或 `generated/patch.yaml`。需要舊式 manifest/configs 時使用 `compile-folder --layout expanded`。

### Compiler exact-byte payload

`compile-folder` 會把產生的 operations 實際套用一次，並與 after 檔案比較原始 bytes。若結構正確但註解、位置、BOM、LF/CRLF、multi-document 排版或 XML mixed content 無法逐 byte 重現，compact patch 可能包含：

```yaml
replace_bytes_base64: ...
```

新增檔案可能包含：

```yaml
create_bytes_base64: ...
```

這兩個欄位是 compiler 自動產生的 exact payload，不是日常手寫 operation，通常不應人工修改。它們仍位於唯一的 `patch.yaml`，不會額外產生 payload 檔案。


## Missing policy、key/name selector 與大型 section

需要既有目標的 operation 預設使用 `missing: error`。可明確改成：

```yaml
missing: error
missing: skip
missing: create
```

`create_missing: true` 仍相容，等同 `missing: create`。Pattern selector 不允許 `missing: create`。

YAML mapping 可使用：

```yaml
key: prod
key_pattern: "api-*"
pattern_type: glob   # glob / iglob / regex / iregex
```

YAML list item 與 XML repeated item 可使用：

```yaml
name: api-user
name_pattern: "api-*"
```

XML direct child element 可使用 `name` / `name_pattern`；`key` / `key_pattern` 為同義寫法。

大型 dict、list、指定 index、section copy/move/merge/replace 與 child folder 的完整可複製範例，請見 [Section 與複雜結構 Config 範例](SECTION_CONFIG_EXAMPLES_zh-TW.md)。

## Path wildcard、index 與值匹配

YAML path 支援 `*`、`[*]`、`[N]`、負索引；YAML `[N]` 是 0-based。XML path 支援 `*`、`[*]`、`[N]`、`[@attr='value']`、`[child='value']`；XML `[N]` 是 1-based。

完整範例請參考 `SECTION_CONFIG_EXAMPLES_zh-TW.md` 的「Wildcard、索引與陣列值匹配」。


## Compact folder patch 搭配 variable-map.yaml

```yaml
version: 1
kind: yaml-folder-patch-compact
variable_map_file: variable-map.yaml
files:
  FAB14-FZ1/STAGING/app/application.yaml:
    ops:
      - set: [$.server.host, "{{ HOST }}"]
      - op: set
        path: $.runtime
        missing: create
        value:
          timeout: "{{ TIMEOUT }}"
          retry: [1, 5, 15]
```

外部檔：

```yaml
variable_map:
  FAB14:STAGING:
    HOST: staging-host
    TIMEOUT: 45
  FAB14-FZ1:STAGING:
    HOST: fz1-staging-host
```

外部檔路徑以 `patch.yaml` 所在目錄為基準。CLI `--var` 仍具有最高優先權。

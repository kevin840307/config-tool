# YAML/XML Config 完整使用手冊

本文件集中說明可手寫與 Auto compiler 可能產生的所有主要 Config 結構。第一次使用可先看「最小範例」；需要精確控制時再查各 operation。

## 1. 最小範例

```yaml
version: 1
defaults_profile: concise-v1
operations:
  - op: replace
    path: $/server/port
    value: 9090
```

執行：

```bash
python yaml_config_tool.py apply before.yaml patch.yaml -o result.yaml
python yaml_config_tool.py verify before.yaml patch.yaml after.yaml
```

Auto compile：

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml
```

## 2. Config 頂層欄位

```yaml
version: 1
defaults_profile: concise-v1
variables: {}
variable_map_file: []
variable_map: {}
options: {}
defaults: {}
documents: null
operations: []
rules: []
```

| 欄位 | 用途 |
|---|---|
| `version` | Config schema 版本，目前為 `1` |
| `defaults_profile` | `concise-v1` 允許省略安全預設 |
| `variables` | Config 內直接宣告的變數 |
| `variable_map_file` | 一個或多個外部 mapping 檔 |
| `variable_map` | FAB/ENV scope mapping |
| `options` | atomic write、YAML/XML 輸出格式 |
| `defaults` | 套用到所有 operation 的共同欄位 |
| `documents` | Multi-document YAML selector |
| `operations` | 單檔 operation 清單 |
| `rules` | Folder rule 清單 |

未使用的欄位可省略。

## 3. `concise-v1` 預設

Auto compiler 預設產生：

```yaml
defaults_profile: concise-v1
```

它只省略可推導欄位，不改舊 Config 語意。

`replace_value` 省略時等同：

```yaml
count: 1
expect_replacements: 1
```

需要全部替換時明寫：

```yaml
count: -1
```

舊 Config 沒有 `defaults_profile` 時維持舊版行為。

## 4. Path 語法

### 4.1 YAML path

建議使用 `/` 形式：

```text
$/server/port
$/apps/appA/enabled
$/services/0/name
```

也支援：

```text
$.server.port
$.services[0].name
```

特殊 key 使用 JSON Pointer escaping：

```text
key `a/b`  → $/a~1b
key `a~b`  → $/a~0b
```

### 4.2 Wildcard

```text
$/apps/*/enabled
$/apps/*/phases/*/versions
$/containers[*]/resources
```

- `*`：mapping child 或一層 selector。
- `[*]`：list 中全部 item。
- `[N]`：指定 list index，YAML 為 0-based。
- 負 index：`[-1]` 表示最後一筆。

多條不規則 path：

```yaml
paths:
  - $/apps/appA/phases/p1/versions
  - $/apps/appA/phases/f13p1/versions
```

同一 operation 不可同時存在 `path` 和 `paths`。

### 4.3 XML path

```text
/configuration/server/port
/configuration/appSettings/add/@value
/application/versions
```

XML attribute 以 `@` 表示。XML `[N]` 為 1-based。

## 5. Variables 與 mapping

### 5.1 一般變數

```yaml
variables:
  PORT: 9090
operations:
  - op: replace
    path: $/server/port
    value: "{{ PORT }}"
```

完整 expression 可保留型別：

```yaml
value: "{{ REPLICAS }}"
```

當 `REPLICAS` 是 integer，結果仍是 integer。

### 5.2 外部 mapping

```yaml
variable_map_file:
  - variables/common.yaml
  - variables/version.yaml
```

外部檔：

```yaml
variable_map:
  FAB13:STAGING:
    old_version: v507
    current_version: v509
    new_version: v510
```

套用：

```bash
python yaml_config_tool.py apply before.yaml patch.yaml -o result.yaml \
  --variable-map-file variables/version.yaml \
  --fab FAB13 --env STAGING
```

Scope 越精確優先度越高，例如：

```text
FAB13
FAB13:STAGING
FAB13-FZ1
FAB13-FZ1:STAGING
```

FAB 不會由工具自行推斷或改名；版本生命週期也由 mapping 明確提供。

### 5.3 內建 folder 變數

```text
FAB
ENV
PATH
RELATIVE_PATH
APP_PATH
FILE_NAME
FILE_STEM
```

缺少模板變數會直接失敗，不會寫入 `Undefined` 或空值。

## 6. 共通 operation 欄位

```yaml
- op: replace
  path: $/server/port
  value: 9090
  missing: error
```

常用欄位：

| 欄位 | 說明 |
|---|---|
| `op` | operation 名稱 |
| `path` / `paths` | 目標 selector |
| `missing` | `skip`、`error`、`create` |
| `expect_matches` | 預期 match 數；`-1` 表示全部 |
| `on_multiple_matches` | `error` 或 `all` |
| `quote` | `auto`、`preserve`、`plain`、`single`、`double` |
| `quote_styles` | 巢狀 payload 的 quote style |

主要 operation 預設 `missing: skip`。重要人工 Config 建議使用：

```yaml
missing: error
expect_matches: 1
```

Pattern path 不支援 `missing: create`。

## 7. Scalar 與 node operations

### 7.1 `set` / `replace`

修改既有值：

```yaml
- op: replace
  path: $/server/port
  value: 9090
```

建立缺少的路徑：

```yaml
- op: set
  path: $/feature/newRouting
  value: true
  missing: create
```

### 7.2 `replace_value`

只替換字串中的片段：

```yaml
- op: replace_value
  path: $/image/tag
  search: "v509"
  replacement: "{{ new_version }}"
```

全部替換：

```yaml
count: -1
```

### 7.3 `remove`

```yaml
- op: remove
  path: $/legacy
  missing: skip
```

### 7.4 `merge`

```yaml
- op: merge
  path: $/resources/requests
  value:
    cpu: 500m
    memory: 1Gi
  strategy: overwrite
```

Mapping strategy：

```text
overwrite
keep_existing
delete_null
```

List strategy：

```text
append
prepend
unique
```

### 7.5 `capture`

```yaml
- op: capture
  path: $/version
  name: OLD_VERSION
- op: replace
  path: $/backup/version
  value: "{{ OLD_VERSION }}"
```

## 8. Mapping key operations

### 8.1 `insert_key`

```yaml
- op: insert_key
  path: $/server
  key: timeout
  value: 30
  position:
    after_key: host
```

位置可用：

```yaml
position: {before_key: port}
position: {after_key: host}
position: {index: 1}
position: {first: true}
position: {last: true}
```

### 8.2 `rename_key`

```yaml
- op: rename_key
  path: $/server
  old_key: port
  new_key: httpPort
```

### 8.3 `copy_key`

```yaml
- op: copy_key
  path: $/server
  source_key: host
  target_key: backupHost
  position: {after_key: host}
  on_conflict: error
```

### 8.4 `move_key`

```yaml
- op: move_key
  path: $/server
  source_key: timeout
  target_key: timeout
  position: {after_key: host}
```

### 8.5 `copy_node` / `move_node`

```yaml
- op: copy_node
  from_path: $/templates/latest
  to_path: $/versions/v510
  missing: create
```

```yaml
- op: move_node
  from_path: $/legacy/config
  to_path: $/config
  missing: create
```

## 9. List operations

### 9.1 `append` / `prepend`

```yaml
- op: append
  path: $/ports
  value: 9090
```

```yaml
- op: prepend
  path: $/profiles
  value: base
```

### 9.2 `insert` / `insert_at`

```yaml
- op: insert_at
  path: $/services
  index: 1
  value:
    name: worker
```

越界策略：

```yaml
position:
  index: 99
  on_out_of_range: append
```

或 `clamp`。

### 9.3 `insert_before` / `insert_after`

```yaml
- op: insert_after
  path: $/services
  match:
    name: api
  expect_matches: 1
  value:
    name: worker
```

## 10. Item operations

### 10.1 `update_item`

```yaml
- op: update_item
  path: $/versions
  match:
    name: "{{ current_version }}"
  expect_matches: 1
  missing: error
  set:
    shadow: false
    replicas: 2
```

深層欄位可放在 `set`：

```yaml
set:
  resources.requests.cpu: 500m
  autoscaling.maxReplicas: 20
```

或使用 `item_operations`：

```yaml
item_operations:
  - op: replace
    path: $/resources/requests/cpu
    value: 500m
  - op: append
    path: $/config/spring/profiles
    value: observability
```

更新容器中全部既有 item：

```yaml
- op: update_item
  path: $/services
  expect_matches: -1
  set:
    enabled: true
```

沒有 selector 時必須明確使用 `expect_matches: -1`。

### 10.2 `upsert_item`

```yaml
- op: upsert_item
  path: $/services
  match: {name: api}
  set: {enabled: true}
  value: {name: api, enabled: true}
  position: {last: true}
```

有命中就更新，沒有命中就新增。

### 10.3 `remove_item`

```yaml
- op: remove_item
  path: $/versions
  match:
    name: "{{ old_version }}"
  expect_matches: 1
  missing: error
  remove_leading_comments: true
```

### 10.4 `copy_item`

版本升級常用：

```yaml
- op: copy_item
  path: $/versions
  from:
    name: "{{ current_version }}"
  after:
    name: "{{ current_version }}"
  set:
    name: "{{ new_version }}"
    shadow: true
```

Canonical 完整寫法：

```yaml
- op: copy_item
  path: $/versions
  source:
    match: {name: "{{ current_version }}"}
    expect_matches: 1
  position:
    after:
      match: {name: "{{ current_version }}"}
      expect_matches: 1
  set:
    name: "{{ new_version }}"
```

防止重複：

```yaml
duplicate:
  unique_by: [name]
  policy: error
```

Policy：`allow`、`skip`、`update`、`error`。

### 10.5 `move_item`

```yaml
- op: move_item
  path: $/services
  match: {name: worker}
  position: {first: true}
```

### 10.6 `copy_item_to_node`

```yaml
- op: copy_item_to_node
  from_path: $/versions
  to_path: $/templates/latest
  source:
    match: {name: "{{ current_version }}"}
    expect_matches: 1
  set:
    name: template
```

## 11. Match 語法

基本 match：

```yaml
match:
  name: api
  enabled: true
```

邏輯：

```yaml
match:
  all:
    - {enabled: true}
    - {phase: p1}
```

```yaml
match:
  any:
    - {name: appA}
    - {name: appB}
```

比較 operator：

```yaml
match:
  replicas: {$gte: 2}
  name: {$glob: "app-*"}
```

支援：

```text
$eq $ne $in $not_in $contains
$starts_with $ends_with
$glob $iglob $regex $iregex
$gt $gte $lt $lte $between
$type $exists
```

Pattern 簡寫：

```yaml
name_pattern: "app-*"
pattern_type: glob
```

Mapping key 可用：

```yaml
key_pattern: "phase-*"
pattern_type: regex
```

Regex 非法時會直接失敗。

## 12. Position 簡寫

```yaml
place: top
place: bottom
place: 2
place: {after_key: host}
place: {before: {name: appA}}
place: {after: {name: appA}}
```

Auto compiler 也可能輸出：

```yaml
before: {name: appA}
after: {name: appA}
```

## 13. Quote 與型別

```yaml
- op: replace
  path: $/code
  value: "001"
  quote: double
```

巢狀樣式：

```yaml
quote_styles:
  value.version: single
  value.endpoint: double
```

Auto compiler會保留：

- string / integer / float / boolean / null 型別。
- plain / single / double quote。
- literal `|`、folded `>` block scalar。
- mapping/list 順序。
- comments、anchor/alias（rc27 起修正 merge-anchor replay）。

字串：

```yaml
"false"
"001"
"2026-07-16"
```

不會誤轉成 boolean、integer 或 date。

## 14. YAML anchor / alias

```yaml
defaults: &defaults
  enabled: true
  mode: safe
apps:
  appA:
    <<: *defaults
```

Auto compiler 只應修改 anchor source：

```yaml
- op: replace
  path: $/defaults/mode
  value: strict
```

不應將繼承欄位展開到每個 app。rc27 已加入 merge-aware clone 與 replay comparison，避免 anchor/alias 被 materialize。

## 15. Multi-document YAML

```yaml
---
kind: ConfigMap
metadata: {name: app}
---
kind: Deployment
metadata: {name: app}
```

可用 `documents` 限制 document；Auto compiler 無法安全拆分時會使用完整 multi-document replacement，確保結果正確。

## 16. Folder patch

### 16.1 Compile / apply

```bash
python config_tool.py compile-folder before after generated
python config_tool.py apply-folder before generated output
python config_tool.py verify-folder before generated after
```

Compact patch：

```yaml
version: 1
kind: yaml-folder-patch-compact
files:
  values.yaml:
    format: yaml
    config:
      version: 1
      operations: []
  config/appA/application.yaml:
    format: yaml
    config:
      version: 1
      operations: []
  config/appA/log.xml:
    format: xml
    config:
      version: 1
      operations: []
```

Folder action：

```text
patch
create
delete
unchanged
```

### 16.2 File key template / glob

```yaml
files:
  "config/appB/{{ current_version }}/application.yaml":
    format: yaml
    config: {}
  "config/*/application.yaml":
    format: yaml
    config: {}
  "**/log.xml":
    format: xml
    config: {}
```

版本 folder 可由外部流程先建立；工具只負責檔案內容。

### 16.3 Exact-byte fallback

若 comments、BOM、換行、XML mixed content 無法用 operation 逐 byte 還原，Auto folder patch 可能產生：

```yaml
replace_bytes_base64: ...
create_bytes_base64: ...
```

這是 compiler 的正確性 fallback，不建議手動撰寫。

## 17. Folder rules

```yaml
version: 1
rules:
  - id: staging-app
    priority: 100
    filters:
      path_allow:
        - "config/**/application.yaml"
      path_deny:
        - "config/archive/**"
      fab_allow_prefix:
        - FAB13
      env_allow:
        - STAGING
    operations:
      - op: replace
        path: $/server/timeout
        value: 45
```

Filter：

```text
path_allow
path_deny
fab_allow_prefix
fab_deny_prefix
env_allow
env_deny
```

Deny 優先。`priority` 越高越早執行；同 priority 依宣告順序。`stop: true` 可停止後續 rules。

## 18. XML 簡單範例

修改 attribute：

```yaml
- op: set
  path: /configuration/server/@enabled
  value: "true"
```

更新重複 element：

```yaml
- op: update_item
  path: /application/versions
  element: version
  match:
    '@id': "{{ current_version }}"
  set:
    '@shadow': "false"
```

新增版本：

```yaml
- op: copy_item
  path: /application/versions
  element: version
  source:
    match: {'@id': "{{ current_version }}"}
  set:
    '@id': "{{ new_version }}"
```

更多 XML 細節見 `XML_USER_GUIDE_zh-TW.md`。

## 19. 常見版本升級完整範例

```yaml
version: 1
defaults_profile: concise-v1
operations:
  - op: remove_item
    path: $/apps/appA/phases/*/versions
    match:
      name: "{{ old_version }}"
    missing: error

  - op: copy_item
    path: $/apps/appA/phases/*/versions
    from:
      name: "{{ current_version }}"
    after:
      name: "{{ current_version }}"
    set:
      name: "{{ new_version }}"
      shadow: true

  - op: update_item
    path: $/apps/appA/phases/*/versions
    match:
      name: "{{ current_version }}"
    set:
      shadow: false
    item_operations:
      - op: replace
        path: $/resources/requests/cpu
        value: 500m
      - op: replace
        path: $/autoscaling/maxReplicas
        value: 20
      - op: replace
        path: $/config/featureFlags/newRouting
        value: true
```

版本值來自 mapping；工具不自行判斷哪個是最舊、目前或新版。

## 20. Auto compiler 輸出原則

Auto compiler 依序追求：

1. Apply 後與 after 100% 相同。
2. 合併完全相同的 operation。
3. 優先使用 `*`、`[*]`。
4. Wildcard 不安全時使用 union 或 `paths`。
5. 共同設定合併，例外保留精確 path。
6. 版本與內容由 mapping 泛化。
7. 省略 `concise-v1` 預設欄位。
8. 每個候選都 replay；失敗即回退。

它不會為了更短而犧牲 operation 順序或正確性。

## 21. 錯誤處理建議

人工 Config 建議：

```yaml
missing: error
expect_matches: 1
```

可接受檔案差異或不存在時才用：

```yaml
missing: skip
```

以下會直接失敗：

- YAML 重複 key。
- 缺少模板變數。
- 非法 regex。
- `update_item` 指向非 list。
- 未允許的 index 越界。
- Selector path 搭配 `missing: create`。
- `missing: error` 下 match 數不符。

Atomic write 開啟時，apply 失敗不會覆蓋既有 output。

## 22. 推薦驗證流程

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml
python yaml_config_tool.py verify before.yaml patch.yaml after.yaml
python yaml_config_tool.py apply before.yaml patch.yaml -o result.yaml
```

Folder：

```bash
python config_tool.py compile-folder before after generated
python config_tool.py verify-folder before generated after
```

發布前：

```bash
python self_test.py
python tests/test_enterprise_yaml_scenario_matrix.py
python tests/test_enterprise_yaml_error_matrix.py
python tests/test_enterprise_helm_values_auto.py
python tests/test_large_complex_auto.py
```

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

更新目前 list／重複 XML element 中的**全部既有項目**時，可省略 `match`，但必須明確設定 `expect_matches: -1`：

```yaml
- op: update_item
  path: $.services
  expect_matches: -1
  set:
    enabled: true
    runtime.timeout: 45
```

XML：

```yaml
- op: update_item
  path: /configuration/services
  element: service
  expect_matches: -1
  set:
    '@enabled': 'true'
    runtime.timeout: '45'
```

規則：

- 有 `match/name/name_pattern`：只更新符合條件的項目。
- 沒有 selector 且 `expect_matches: -1`：更新目前容器中的全部項目。
- 沒有 selector 且不是 `-1`：直接報錯，避免意外更新全部。
- `path` 應指向 list／重複節點的容器；可使用 `$.applications.*.services` 讓多個容器各自更新全部 item。
- 欄位名稱是 `expect_matches`，不是 `expect_matchs`。

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

所有主要 operation 預設使用 `missing: skip`。找不到完整 selector chain 時不失敗，CLI 會在 `skipped_operations` 顯示資訊。需要嚴格驗證時可明確改成：

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

## 字串內容替換：`replace_value`

`replace` 是替換整個 YAML/XML 節點；只想替換字串中的一部分時，使用 `replace_value`。

YAML：

```yaml
operations:
  - op: replace_value
    path: $.server.url
    search: /aaa
    replacement: /bbb
    expect_replacements: 1
```

來源值 `abdc/aaa` 會變成 `abdc/bbb`，其他內容不變。

同時更新多個 YAML value：

```yaml
operations:
  - op: replace_value
    path: $.opt.*.url
    search: /v506/
    replacement: /v512/
    on_no_match: skip
    on_multiple_matches: all
```

Regex：

```yaml
operations:
  - op: replace_value
    path: $.services[*].image
    search: ':[0-9]+\.[0-9]+$'
    replacement: ':2.0'
    pattern_type: regex
    on_no_match: skip
```

XML attribute 或 element text 使用相同格式：

```yaml
operations:
  - op: replace_value
    path: /configuration/server/@url
    search: /aaa
    replacement: /bbb

  - op: replace_value
    path: /configuration/server/path
    search: /aaa
    replacement: /bbb
```

可用欄位：

- `search`：要尋找的文字；別名 `old`
- `replacement`：替換文字；別名 `new`
- `pattern_type`：`literal`（預設）、`regex`、`iregex`
- `count`：最多替換次數，`0` 表示全部
- `expect_replacements`：實際替換次數必須相同
- `on_no_match`：`error`（預設）或 `skip`
- `missing`：路徑不存在時 `error` 或 `skip`

## 自動 compiler 的 selector 簡化

自動 compiler 會先逐筆產生精確 operation，再嘗試把完全相同的變更合併成 wildcard path。

例如：

```yaml
$.opt.appA.enabled
$.opt.appB.enabled
$.opt.appC.enabled
```

會安全收斂成：

```yaml
- op: replace
  path: $.opt.*.enabled
  value: true
  on_multiple_matches: all
```

多層共同變更也可收斂：

```yaml
- op: replace
  path: $.applications.*.routes[*].metadata
  value:
    managed: true
  on_multiple_matches: all
```

安全規則：

1. operation、value、missing 與其他參數必須完全相同。
2. wildcard 實際命中的節點集合必須等於原本逐筆 path。
3. 合併後會重新 apply，結果必須與 after 完全相同。
4. 任何條件不成立就保留精確 path，不會強行簡化。

字串 scalar 的 before/after 只改一部分時，compiler 會在安全且直觀的情況下產生 `replace_value`。

但以下完整數字或數字版本值會直接產生完整 `replace`（YAML）或 `set`（XML），不會使用局部字串替換：

```yaml
# before: "2026.04"
# after:  "2026.05"
- op: replace
  path: $/version
  value: "2026.05"

# before: 30
# after:  45
- op: replace
  path: $/timeout
  value: 45
```

只有像 `abdc/aaa` 變成 `abdc/bbb` 這類一般字串片段，才會自動產生 `replace_value`。

## 自動 compiler：共同差異抽取

自動 compiler 不只會合併整筆完全相同的 operation，也會把同一個 dict/list 內的「共同部分」抽成 wildcard operation，個別不同部分則保留精確設定。

例如三個 mapping 都新增相同 `retry`、共同修改 `timeout`，但 `endpoint` 不同：

```yaml
opt:
  appA: {timeout: 30}
  appB: {timeout: 30}
  appC: {timeout: 30}
```

會自動收斂成：

```yaml
operations:
  - op: set
    path: $/opt/*/retry
    missing: create
    on_multiple_matches: all
    value:
      count: 3
      delays: [1, 5, 15]

  - op: replace
    path: $/opt/*/timeout
    value: 45
    on_multiple_matches: all

  - op: insert_key
    path: $/opt/appA
    key: endpoint
    value: api-a

  - op: insert_key
    path: $/opt/appB
    key: endpoint
    value: api-b
```

List item 也會抽取共同欄位：

```yaml
operations:
  - op: set
    path: $/services/*/retry
    missing: create
    on_multiple_matches: all
    value:
      count: 3

  - op: replace
    path: $/services/*/timeout
    value: 45
    on_multiple_matches: all

  - op: update_item
    path: $/services
    match: {name: api}
    item_operations:
      - op: replace
        path: $/image
        value: api:v2
```

安全原則：

1. 先產生逐項精確 operations。
2. 僅抽取內容、operation 與語意完全一致的共同部分。
3. 不同部分仍保留精確 path 或 `update_item`。
4. 簡化後會重新套用完整 config；結果與 after 不完全一致就退回原始逐項 operations。
5. 超大型 diff 會限制高成本候選分析，避免 config 簡化拖慢上千行 YAML 的編譯。

## `missing` 對中間路徑的統一行為

`missing` 不只判斷最後一個 key。當中間 parent path、來源節點、陣列 selector 或 match 不存在時，主要 YAML/XML operation 都採相同語意：

```yaml
missing: skip    # 預設：略過並回報資訊
missing: skip    # 整筆 operation 不執行，文件保持不變
missing: create  # 僅在可明確推導容器類型或節點名稱時建立
```

例如 `insert_key` 的中間 parent 不存在時可安全略過：

```yaml
- op: insert_key
  path: $.application.optional.runtime
  key: retry
  value:
    count: 3
    delays: [1, 5, 15]
  missing: skip
```

需要建立完整 mapping parent 時：

```yaml
- op: insert_key
  path: $.application.runtime
  key: retry
  value:
    count: 3
  missing: create
```

List 容器也能明確建立：

```yaml
- op: append
  path: $.application.routes
  value:
    name: health
    path: /health
  missing: create
```

`missing: skip` 適用於 `set/replace/remove/merge/rename_key/insert_key/copy_key/move_key/copy_node/move_node/append/prepend/insert/update_item/upsert_item/remove_item/move_item/copy_item/capture/replace_value` 等主要操作。`missing: create` 只適用於能安全判斷新增內容的操作；例如 remove、rename、copy source 不存在時無法猜測來源，因此不可建立。


## Compiler 多階段收斂

自動 compiler 不是只做一次簡化，而是執行最多 6 輪的 verified fixed-point pipeline：共同 section 抽取、list item 共同欄位抽取、`*`/`[*]` 合併、同 parent 欄位合併成 `merge`。每一輪都會完整 replay；只要結果與 after 不等價，就保留上一輪安全結果。大型差異會停用高成本候選搜尋，但仍保留線性 wildcard 與 merge 收斂。

## 精確 Key Union Selector

Auto compiler 在多個 sibling mapping/list 具有完全相同行為時，可產生精確 union selector：

```yaml
path: $/test-data/[p1,p2,p3]
```

它只會展開為：

```text
$/test-data/p1
$/test-data/p2
$/test-data/p3
```

與 `*` 不同，不會選到同層其他 key。這讓 compiler 能安全合併 `copy_item`、`remove_item`、`update_item`、`move_item`、`insert_key` 與一般 scalar operations。

Auto optimizer 採多輪 checkpoint/replay：每一輪候選必須完整重播並嚴格等於 after（包含 dict/list 順序與 scalar 型別）才接受；候選失敗或 optimizer 發生例外，只回退該輪，不會破壞前一個已驗證結果。

## Auto compiler 可讀簡寫

Auto compiler 可能輸出以下簡寫；載入時會轉成 canonical operation：

- `from`：`copy_item.source.match` 的簡寫
- `before` / `after`：list item 相對位置簡寫
- `place`：mapping key 或 index 位置簡寫
- `set`：直接修改 matched/copied item 欄位
- `merge`：遞迴合併 matched/copied item 的巢狀 mapping

`merge` 只在 replay 可證明與 after 完全一致時使用；若新增 key 的順序不能正確還原，compiler 會保留原本的 `item_operations`。

## Auto compiler 當下結構收斂規則（v0.9.4）

Auto compiler 不推測未來可能新增的節點，只依目前 before/after 判斷：

- 同一 mapping 目前所有 child 都有相同行為：使用 `*`。
- 同一 list 目前所有 item 都有相同行為：使用 `[*]`。
- 只有部分 sibling 有相同行為：使用 `[p1,p2,p3]` 等明確 union。
- 候選必須 replay 後 100% 等於 after，包含值、型別、mapping 順序與 list 順序。
- replay 通過但 config 沒有變短、重複沒有減少或可讀性變差時，不接受該候選。
- 某一輪失敗只回退該輪，不影響前面已驗證的收斂結果。


# Quote style

Operation 可選：

```yaml
quote: auto | preserve | plain | single | double
```

`auto`/未設定為預設。`quote_styles` 用於巢狀 payload，path 第一段為 `value`、`set`、`merge`、`replacement`：

```yaml
quote_styles:
  set.version: single
  merge.metadata.tag: double
  value.items.0.name: plain
```

Auto compiler 只在必要時輸出 quote metadata；一般情況不增加設定。`plain` 若會改變 YAML 型別，strict replay 會拒絕候選並回退。

## Folder `files:` key 的變數與 wildcard

Compact folder patch 的 `files:` key 可使用變數：

```yaml
version: 1
kind: yaml-folder-patch-compact
variables:
  version: v512
files:
  "{{ version }}/application.yaml":
    ops:
      - set: [$/app/version, "{{ version }}"]
```

也可使用 wildcard：

```yaml
files:
  "*/application.yaml":       # 僅匹配一層目錄
    ops:
      - set: [$/enabled, true]

  "**/logging.yaml":          # 遞迴匹配任意層級
    ops:
      - set: [$/level, INFO]
```

支援 `*`、`?`、`[]` 與 `**`。規則如下：

- wildcard 只匹配套用來源中已存在的檔案，不會憑空建立多個未知路徑。
- 變數展開後若是具體路徑，可配合 `create_documents`、`create_text` 等建立新檔。
- file-key template 可使用 patch `variables`、`variable_map.global` 與 CLI/API runtime variables。
- 具體檔案確定後，檔案內容 operations 仍會依 FAB/ENV scope 解析變數。
- 兩個 file key 若同時匹配同一檔案，會明確報 conflict，不依設定順序偷偷覆蓋。
- 絕對路徑與 `../` path traversal 會被拒絕。

此能力同時適用於：

- `yaml-folder-patch-compact`
- `xml-folder-patch-compact`
- `mixed-folder-patch-compact`

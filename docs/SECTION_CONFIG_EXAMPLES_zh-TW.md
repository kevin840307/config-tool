# Section 與複雜結構 Config 範例

本章專門說明如何手寫 config 修改「完整 section」，而不是只更新單一 value。所有範例都可放在主 config 的 `operations:` 或 child rule 的 `operations:` 中。

## 1. 找不到目標時要怎麼處理

所有需要既有目標的操作，預設都是：

```yaml
missing: error
```

可選三種模式：

```yaml
missing: skip    # 預設；找不到就略過，CLI 會列出 skipped_operations
missing: skip    # 找不到就略過，適合不同版本不一定都有的 optional section
missing: create  # 找不到就建立，適合明確知道要新增的 key/element
```

舊寫法仍可用：

```yaml
create_missing: true   # 等同 missing: create
```

若 `missing` 與 `create_missing` 同時設定且互相衝突，工具會直接報錯。

Pattern 不能搭配 `missing: create`，因為 `api-*` 無法推導實際要新增的名稱。

---

## 2. YAML：新增完整 dict + list section

原始檔只有：

```yaml
applications:
  web:
    replicas: 2
```

Config：

```yaml
operations:
  - op: set
    path: $.applications.order-service
    missing: create
    value:
      image:
        repository: registry/order
        tag: 2.4.0
      replicas: 3
      env:
        - name: JAVA_OPTS
          value: -Xms512m -Xmx1g
        - name: FEATURE_MODE
          value: strict
      resources:
        requests:
          cpu: 250m
          memory: 512Mi
        limits:
          cpu: "1"
          memory: 1Gi
      routes:
        - path: /orders
          methods: [GET, POST]
        - path: /orders/{id}
          methods: [GET, PUT, DELETE]
```

這會一次建立 `order-service` 的完整 section，包含巢狀 dict 與 list。

---

## 3. YAML：只有 key 存在才替換整個 section

```yaml
operations:
  - op: replace
    path: $.profiles
    key: prod
    missing: skip
    value:
      database:
        host: prod-db
        pool:
          min: 5
          max: 30
      cache:
        enabled: true
        ttlSeconds: 120
      features:
        - audit
        - metrics
        - rate-limit
```

`path` 指向父 mapping，`key` 指定要處理的 child key。`prod` 不存在時完全不動。

要不存在時自動建立：

```yaml
operations:
  - op: set
    path: $.profiles
    key: prod
    missing: create
    value:
      database:
        host: prod-db
```

---

## 4. YAML：依 key pattern 更新多個完整 section

```yaml
operations:
  - op: set
    path: $.services
    key_pattern: "api-*"
    pattern_type: glob
    value:
      deployment:
        replicas: 4
        strategy:
          type: RollingUpdate
          maxUnavailable: 0
      observability:
        metrics: true
        tracing:
          enabled: true
          sampleRate: 0.2
      ports:
        - name: http
          containerPort: 8080
        - name: management
          containerPort: 9090
```

這會更新 `api-user`、`api-order` 等 key，但不會修改 `batch-cleanup`。

支援：

```yaml
pattern_type: glob    # api-*
pattern_type: iglob   # glob，忽略大小寫
pattern_type: regex   # ^api-(user|order)$
pattern_type: iregex  # regex，忽略大小寫
```

Pattern 零命中時略過：

```yaml
missing: skip
```

Pattern 多筆命中預設全部處理；需要強制只能一筆：

```yaml
on_multiple_matches: error
```

---

## 5. YAML：指定 list index 新增完整 item

```yaml
operations:
  - op: insert_at
    path: $.pipelines
    index: 1
    value:
      name: security-scan
      enabled: true
      stages:
        - name: dependency-check
          timeout: 300
        - name: sast
          timeout: 600
      failurePolicy:
        action: stop
        notify: [security-team, project-owner]
```

其他位置寫法：

```yaml
- op: prepend
  path: $.pipelines
  value: {name: bootstrap, enabled: true}

- op: append
  path: $.pipelines
  value: {name: cleanup, enabled: true}

- op: insert_before
  path: $.pipelines
  match: {name: deploy}
  value:
    name: approval
    approvers: [qa, owner]

- op: insert_after
  path: $.pipelines
  match: {name: build}
  value:
    name: package
    artifacts: [app.jar, checksum.txt]
```

---

## 6. YAML：依 name pattern 更新 list 中多個大型 item

```yaml
operations:
  - op: update_item
    path: $.components
    name_pattern: "api-*"
    pattern_type: glob
    on_multiple_matches: all
    missing: skip
    set:
      config.timeout: 45
      config.retry:
        count: 5
        backoff: [1, 5, 15]
      config.circuitBreaker:
        enabled: true
        failureThreshold: 10
      config.headers:
        X-Service-Mode: strict
        X-Trace-Enabled: "true"
```

精確指定單一 name：

```yaml
operations:
  - op: update_item
    path: $.components
    name: api-user
    set:
      config.timeout: 30
```

原本的 `match` 仍可使用：

```yaml
operations:
  - op: update_item
    path: $.components
    match:
      name: api-user
      enabled: true
    set:
      config.resources:
        cpu: "1"
        memory: 1Gi
```

---

## 7. YAML：找不到就新增 list item

使用 `upsert_item`，而不是 `update_item + missing:create`：

```yaml
operations:
  - op: upsert_item
    path: $.components
    match:
      name: audit-worker
    value:
      name: audit-worker
      enabled: true
      config:
        queues: [audit-high, audit-low]
        concurrency: 4
        retry:
          count: 5
          delaySeconds: 10
    position:
      last: true
```

`update_item` 只更新既有 item；找不到時可設 `missing: skip`，但不會猜測新 item 的完整內容。

---

## 8. YAML：merge 既有 section 或自動建立

```yaml
operations:
  - op: merge
    path: $.application.runtime
    missing: create
    strategy: overwrite
    value:
      logging:
        level: INFO
        appenders: [console, rolling-file]
      health:
        endpoints: [liveness, readiness]
      limits:
        maxConnections: 200
        requestTimeoutSeconds: 30
```

常用策略：

```yaml
strategy: overwrite
strategy: keep_existing
strategy: delete_null
strategy: append
strategy: prepend
strategy: unique
```

---

## 9. YAML：複製完整版本 section 再修改

```yaml
operations:
  - op: copy_item
    path: $.versions
    source:
      match: {version: "2026.01"}
      expect_matches: 1
    position:
      after:
        match: {version: "2026.01"}
    set:
      version: "2026.07"
      status: candidate
      parameters.newParameter: "{{ NEW_PARAMETER }}"
      sections.featureFlags:
        enabled: true
        mode: safe
    remove:
      - parameters.deprecatedFlag
    duplicate:
      unique_by: [version]
      policy: skip
    copy_leading_comments: false
```

這類操作適合「複製目前最新版 → 改版本 → 新增參數與 section」。

---

## 10. XML：新增完整 section

```yaml
operations:
  - op: set
    path: /configuration/orderService
    missing: create
    value:
      image:
        repository: registry/order
        tag: 2.4.0
      resources:
        requests:
          cpu: 250m
          memory: 512Mi
        limits:
          cpu: "1"
          memory: 1Gi
      routes:
        route:
          - path: /orders
            method: GET
          - path: /orders
            method: POST
```

會建立：

```xml
<orderService>
  <image>
    <repository>registry/order</repository>
    <tag>2.4.0</tag>
  </image>
  ...
</orderService>
```

---

## 11. XML：指定 element name 替換完整 section

```yaml
operations:
  - op: replace
    path: /configuration/profiles
    name: profile-prod
    missing: skip
    value:
      database:
        host: prod-db
        pool:
          min: "5"
          max: "30"
      cache:
        enabled: "true"
        ttlSeconds: "120"
```

`path` 指向父 element，`name` 指定 direct child element。

不存在時建立：

```yaml
operations:
  - op: set
    path: /configuration/profiles
    name: profile-prod
    missing: create
    value:
      database:
        host: prod-db
```

---

## 12. XML：依 element name pattern 更新多個 section

```yaml
operations:
  - op: set
    path: /configuration/profiles
    name_pattern: "profile-*"
    pattern_type: glob
    value:
      database:
        pool:
          min: "3"
          max: "20"
      features:
        feature:
          - audit
          - metrics
```

Pattern 會處理所有命中的 direct child elements。Pattern 不允許 `missing: create`。

---

## 13. XML：依 item 的 `<name>` pattern 更新巢狀 section

```yaml
operations:
  - op: update_item
    path: /configuration/components
    element: component
    name_pattern: "api-*"
    pattern_type: glob
    on_multiple_matches: all
    missing: skip
    set:
      config.timeout: "45"
      config.retry:
        count: "5"
        backoff:
          seconds: ["1", "5", "15"]
      config.circuitBreaker:
        enabled: "true"
        failureThreshold: "10"
```

工具會在每個 `<component>` 內讀取 `<name>`，只修改 `api-*`。

---

## 14. XML：指定 index 插入完整 item

```yaml
operations:
  - op: insert_at
    path: /configuration/pipelines
    element: pipeline
    index: 1
    value:
      name: security-scan
      enabled: "true"
      stages:
        stage:
          - name: dependency-check
            timeout: "300"
          - name: sast
            timeout: "600"
      failurePolicy:
        action: stop
```

---

## 15. Child folder 套用不同 section config

```yaml
operations:
  - op: merge
    path: $.common
    missing: create
    value:
      logging:
        level: INFO
      audit:
        enabled: true

rules:
  - id: app-a
    filters:
      path_allow: ["app-a/**"]
    operations:
      - op: set
        path: $.application.runtime
        missing: create
        value:
          endpoints:
            primary: https://app-a.example
            backup: https://app-a-backup.example
          features: [order, audit]

  - id: app-b-staging
    filters:
      path_allow: ["app-b/staging/**"]
    operations:
      - op: update_item
        path: $.components
        name_pattern: "api-*"
        missing: skip
        on_multiple_matches: all
        set:
          config.timeout: 90
          config.retry:
            count: 8
            backoff: [5, 15, 30]
```

執行：

```bat
python yaml_config_tool.py run-folder source config.yaml output
```

XML 使用相同 rule 結構，只需把 operation path 改成 XML path。

---

## 16. 快速選擇表

| 需求 | 建議 operation |
|---|---|
| 更新已知 path，找不到要略過 | `set`，預設 `missing:skip` |
| 可選 section，有才更新 | `set/replace/merge + missing:skip` |
| 明確新增完整 section | `set/merge + missing:create` |
| 指定 mapping child key | `key` |
| 一次處理多個 mapping key | `key_pattern` |
| 指定 list item 的 name | `update_item + name` |
| 一次處理多個 list item | `update_item + name_pattern` |
| 找不到 list item 就新增 | `upsert_item` |
| 指定 list 位置新增 | `insert_at/insert_before/insert_after` |
| 複製最新版建立新版本 | `copy_item` |
| 整段 section 合併 | `merge` |
| 整段 section 完全替換 | `replace` |

# Wildcard、索引與陣列值匹配

## YAML 路徑語法

YAML 索引採 **0-based**：第一筆是 `[0]`，第二筆是 `[1]`。

| 語法 | 意義 |
|---|---|
| `*` | Mapping 的所有直接 key，或 list 的所有 item |
| `[*]` | List 的所有 item；和 list 位置上的 `*` 同義 |
| `[N]` | 指定第 N 筆，0-based |
| `[-1]` | 最後一筆 |

### 更新所有 mapping child

```yaml
operations:
  - op: set
    path: $.services.*.enabled
    value: true
```

### 更新每個 app 的第二條 route

```yaml
operations:
  - op: set
    path: $.apps[*].routes[1].enabled
    value: true
```

### 在所有 route 自動新增完整 section

```yaml
operations:
  - op: set
    path: $.apps[0].routes[*].metadata
    missing: create
    value:
      owner: platform
      tags:
        - managed
        - v2
      rollout:
        strategy: canary
        percentages: [10, 30, 100]
```

`missing: create` 只會建立 wildcard 已經匹配到的各個 item 內缺少的精確 suffix。若 wildcard 本身零命中，無法推測要建立幾筆，會拒絕建立；可改用 `missing: skip`。

### 多層 wildcard 替換大型 section

```yaml
operations:
  - op: replace
    path: $.regions.*.clusters[*].runtime.limits
    value:
      cpu: 8
      memory: 16Gi
      policies:
        restart: always
        health: [ready, live]
```

## YAML 依陣列元素值匹配

值匹配使用 `update_item / remove_item / copy_item / move_item` 的 `match`，比把條件寫進 path 更容易閱讀。

```yaml
operations:
  - op: update_item
    path: $.versions
    match:
      status: active
      config.tier:
        $glob: "api*"
    on_multiple_matches: all
    missing: skip
    set:
      config.enabled: false
      config.resources:
        requests:
          cpu: 500m
        limits:
          cpu: "2"
```

支援的常用條件：

```yaml
match:
  name: api-a                    # 等於
  status: {$in: [active, ready]}
  version: {$regex: '^v[2-9]'}
  config.tier: {$glob: 'api-*'}
  retry.count: {$gte: 3}
```

也可組合：

```yaml
match:
  all:
    - status: active
    - any:
        - config.tier: api
        - config.tier: gateway
    - not:
        name: deprecated-api
```

## XML 路徑語法

XML `[N]` 沿用 XPath 習慣，採 **1-based**：第一筆是 `[1]`，第二筆是 `[2]`。

| 語法 | 意義 |
|---|---|
| `*` | 任意直接 child element |
| `[*]` | 該名稱的所有同層 element |
| `[N]` | 第 N 筆，1-based |
| `[@name='x']` | attribute 值匹配 |
| `[status='active']` | direct child element 文字值匹配 |

### 更新所有 app 的第二條 route

```yaml
operations:
  - op: set
    path: /configuration/apps/app[*]/routes/route[2]/enabled
    value: "true"
    on_multiple_matches: all
```

### 任意 element 名稱搭配 attribute 條件

```yaml
operations:
  - op: set
    path: /configuration/apps/*[@name='app-a']/routes/route[*]/@managed
    value: "yes"
    missing: create
    on_multiple_matches: all
```

### 依 child value 匹配多筆

```yaml
operations:
  - op: set
    path: /root/versions/version[status='active']/@reviewed
    value: "yes"
    missing: create
    on_multiple_matches: all
```

### 指定第二筆

```yaml
operations:
  - op: replace
    path: /root/versions/version[2]
    value:
      "@attributes":
        name: v2
      status: active
      config:
        timeout: "45"
        features:
          tracing: "true"
```

## Missing policy 搭配 selector

```yaml
missing: skip    # 預設；零命中就略過並回報
missing: skip    # 零命中略過
missing: create  # 精確 suffix 缺少時建立
```

注意：

- `$.services.*.enabled` 的 `services` 沒有任何 child 時，`missing:create` 不會憑空建立未知 child。
- `$.services.*.metadata` 已匹配到 service，但 `metadata` 不存在時，可以為每個 service 建立。
- Pattern selector 如 `key_pattern: api-*` 或 `name_pattern: api-*` 不允許 `missing:create`，因為實際名稱不明確。
- 多筆匹配需要明確使用 `on_multiple_matches: all`；部分 pattern operation 預設即為 all，但手動寫出較清楚。


## update_item 不寫 match：更新全部既有 item

```yaml
operations:
  - op: update_item
    path: $.applications.*.services
    expect_matches: -1
    set:
      enabled: true
      runtime:
        timeout: 45
        retry: {count: 3}
```

`*` 先展開成每個 `services` list；每個 list 內的全部 item 都會更新。若只想更新部分 item，請改用 `match`、`name` 或 `name_pattern`。

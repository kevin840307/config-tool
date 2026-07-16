# 多版本升級完整案例

這個案例涵蓋常見版本維護流程：

1. 刪除已淘汰版本。
2. 複製目前最新版建立新版本。
3. 修改新版本號與狀態。
4. 再修改仍保留的舊版本參數，確保新版不會誤繼承舊版專屬變更。
5. 在新版本新增參數與 section。
6. 防止第二次執行重複建立新版本。
7. 依 FAB / ENV 與 child 路徑套用。

可執行範例位於：

```text
examples/multi-version/yaml/
examples/multi-version/xml/
```

## YAML 來源

```yaml
# application release matrix
versions:
  # oldest version should be removed
  - version: "2025.10"
    status: deprecated
    parameters:
      timeout: 10
      legacyMode: true

  # latest existing version is the clone source
  - version: "2026.01"
    status: active
    parameters:
      timeout: 20
      retry: 2
    sections:
      logging:
        level: INFO
```

## 外部變數表

```yaml
variable_map:
  FAB14:
    NEW_VERSION: "2026.07"
    OLD_TIMEOUT: 45
  FAB14:STAGING:
    NEW_RETRY: 5
    NEW_FEATURE: staging-feature
```

實際路徑若是 `FAB14-FZ1/STAGING/app/application.yaml`，會先命中 `FAB14`，再由 `FAB14:STAGING` 補上或覆蓋變數。

## YAML config

```yaml
version: 1
variable_map_file: variable-map.yaml

rules:
  - id: upgrade-application
    filters:
      path_allow:
        - "FAB14-FZ1/STAGING/app/application.yaml"
    operations:
      - op: remove_item
        path: $.versions
        match: {version: "2025.10"}
        on_zero_matches: ignore
        remove_leading_comments: true

      # 先複製原始最新版，避免後續舊版專屬修改被新版繼承。
      - op: copy_item
        path: $.versions
        source:
          match: {version: "2026.01"}
          expect_matches: 1
        set:
          version: "{{ NEW_VERSION }}"
          status: candidate
          parameters.retry: "{{ NEW_RETRY }}"
        item_operations:
          - op: insert_key
            path: $.parameters
            key: newParameter
            value: "{{ NEW_FEATURE }}"
            position: {last: true}
          - op: insert_key
            path: $.sections
            key: featureFlags
            value: {enabled: true, mode: safe}
            position: {last: true}
        duplicate:
          unique_by: [version]
          policy: skip
        copy_leading_comments: false
        position:
          after:
            match: {version: "2026.01"}
            expect_matches: 1

      # 新版建立後，才修改保留的舊版。
      - op: update_item
        path: $.versions
        match: {version: "2026.01"}
        expect_matches: 1
        set:
          parameters.timeout: "{{ OLD_TIMEOUT }}"
          parameters.compatibility: legacy-compatible
```

## XML config

XML 使用相同概念，但 attribute match 以 `@` 表示：

```yaml
version: 1
format: xml
variable_map_file: variable-map.yaml

rules:
  - id: upgrade-application
    filters:
      path_allow:
        - "FAB14-FZ1/STAGING/app/application.xml"
    operations:
      - op: remove_item
        path: /application/versions
        element: version
        match: {'@id': '2025.10'}
        on_zero_matches: ignore
        remove_leading_comments: true

      - op: copy_item
        path: /application/versions
        element: version
        source:
          match: {'@id': '2026.01'}
          expect_matches: 1
        set:
          '@id': "{{ NEW_VERSION }}"
          '@status': candidate
          parameters.retry: "{{ NEW_RETRY }}"
          parameters.newParameter: "{{ NEW_FEATURE }}"
          sections.featureFlags.enabled: "true"
          sections.featureFlags.mode: safe
        duplicate:
          unique_by: ['@id']
          policy: skip
        position:
          after:
            match: {'@id': '2026.01'}

      - op: update_item
        path: /application/versions
        element: version
        match: {'@id': '2026.01'}
        expect_matches: 1
        set:
          parameters.timeout: "{{ OLD_TIMEOUT }}"
          parameters.compatibility: legacy-compatible
```

## 執行

YAML：

```bat
python yaml_config_tool.py plan-rules-folder examples\multi-version\yaml\source examples\multi-version\yaml\config.yaml
python yaml_config_tool.py check-idempotency examples\multi-version\yaml\source examples\multi-version\yaml\config.yaml
python yaml_config_tool.py run-folder examples\multi-version\yaml\source examples\multi-version\yaml\config.yaml output-yaml
```

XML：

```bat
python xml_config_tool.py plan-rules-folder examples\multi-version\xml\source examples\multi-version\xml\config.yaml
python xml_config_tool.py check-idempotency examples\multi-version\xml\source examples\multi-version\xml\config.yaml
python xml_config_tool.py run-folder examples\multi-version\xml\source examples\multi-version\xml\config.yaml output-xml
```

## 預期結果

版本順序：

```text
2026.01
2026.07
```

- `2025.10` 已刪除，該版本緊鄰的前置註解也可一併移除。
- `2026.07` 先從原始 `2026.01` 複製，因此保留原本 `timeout: 20`。
- `2026.01.parameters.timeout` 隨後改成 `45`。
- `2026.01` 隨後新增 `compatibility`，新版不會繼承這兩項舊版專屬變更。
- 新版 `retry`、`newParameter`、`featureFlags` 已新增。
- 原始檔中屬於保留版本及文件的註解仍保留。
- 再執行一次不會新增第二份 `2026.07`。

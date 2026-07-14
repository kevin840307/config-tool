# 快速開始

## 1. 安裝

```bat
cd yaml-xml-config-tool
python -m pip install -r requirements.txt
```

Linux 可把 `python` 換成 `python3`。

## 2. 由 before / after 自動產生 YAML patch

目錄：

```text
work/
├─ before-folder/
├─ after-folder/
└─ yaml-xml-config-tool/
```

執行：

```bat
python yaml_config_tool.py compile-folder ..\before-folder ..\after-folder generated
```

主要產物：

```text
generated/
└─ patch.yaml       # 預設唯一產物，供閱讀、修改、套用與版控
```

需要逐檔稽核資料時，才加上：

```bat
python yaml_config_tool.py compile-folder ..\before-folder ..\after-folder generated --layout expanded
```

expanded 模式會額外產生 `manifest.yaml`、`configs/` 與必要 payload。

Compiler 會先嘗試可閱讀的結構化 operations，並實際對來源檔執行後與 after 做 byte-level 比對。若新註解、multi-document 排版、BOM、LF/CRLF 或其他格式無法精確重現，會自動改用同一份 `patch.yaml` 內的 exact-byte fallback。

套用與驗證：

```bat
python yaml_config_tool.py apply-folder ..\before-folder generated ..\output-folder
python yaml_config_tool.py verify-folder ..\before-folder generated ..\after-folder
```

## 3. XML 流程

```bat
python xml_config_tool.py compile-folder ..\before-folder ..\after-folder generated-xml
python xml_config_tool.py apply-folder ..\before-folder generated-xml ..\output-folder
python xml_config_tool.py verify-folder ..\before-folder generated-xml ..\after-folder
```

XML compiler 先嘗試最小區段修改；只有無法安全且精確重現時才使用有標記的 exact fallback。

## 4. 整個資料夾 + child 路徑規則

```yaml
version: 1
operations:
  - op: set
    path: $.common.timeout
    value: 30
    create_missing: true

rules:
  - id: app-a
    filters:
      path_allow: ["app-a/**"]
      path_deny: ["app-a/archive/**"]
    operations:
      - op: replace
        path: $.server.host
        value: app-a-server

  - id: app-b-only
    filters:
      path_allow: ["app-b/application.yaml"]
    operations:
      - op: set
        path: $.server.host
        value: app-b-server
```

先預覽，再安全執行：

```bat
python yaml_config_tool.py plan-rules-folder source config.yaml
python yaml_config_tool.py run-folder source config.yaml output
```

`run-folder` 會執行 lint、規則規劃、冪等性、套用與語法檢查。

## 5. 外部 variable-map.yaml

主 config：

```yaml
variable_map_file: variables/variable-map.yaml
operations:
  - op: set
    path: $.server.host
    value: "{{ HOST }}"
```

`variables/variable-map.yaml`：

```yaml
variable_map:
  FAB14:
    HOST: fab14-server
  FAB14:STAGING:
    HOST: fab14-staging-server
  FAB14-FZ1:STAGING:
    HOST: fab14-fz1-staging-server
```

命令列覆蓋：

```bat
python yaml_config_tool.py run-folder source config.yaml output --var HOST=test-server
```

## 6. 單檔手寫 config

```yaml
version: 1
options:
  yaml_output:
    mapping: 2
    sequence: 4
    offset: 2
    line_ending: preserve
operations:
  - op: replace
    path: $.server.port
    value: 9090
```

```bat
python yaml_config_tool.py apply source.yaml config.yaml -o output.yaml
python yaml_config_tool.py verify source.yaml config.yaml expected.yaml
```

## 7. 執行測試

安裝測試工具後：

```bat
python -m pip install -r requirements-dev.txt
RUN_TESTS_WINDOWS.cmd
```

Linux 使用 `./RUN_TESTS_LINUX.sh`。完整驗收範圍見 [測試矩陣](TEST_MATRIX_zh-TW.md)，上千行案例見 [大型與複雜設定驗證報告](LARGE_SCALE_VALIDATION_zh-TW.md)。

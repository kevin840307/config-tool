# YAML / XML Config Tool

以「修改前檔案 + 修改後檔案」自動產生 patch，或使用手寫 config 批次修改 YAML/XML。工具以保留原始內容為優先：未修改區域的註解、順序、引號、縮排、換行與文字格式不應被無關地改寫。

## 最常用：before / after 資料夾

### YAML

```bat
python yaml_config_tool.py compile-folder before-folder after-folder generated
python yaml_config_tool.py apply-folder before-folder generated\patch.yaml output-folder
python yaml_config_tool.py verify-folder before-folder generated after-folder
```

### XML

```bat
python xml_config_tool.py compile-folder before-folder after-folder generated
python xml_config_tool.py apply-folder before-folder generated\patch.yaml output-folder
python xml_config_tool.py verify-folder before-folder generated after-folder
```

`compile-folder` 會產生主要入口 `patch.yaml`，並保留相容用的 `manifest.yaml` 與獨立 configs。新增、刪除、修改與未變更檔案均會分類記錄。

## 常用：整個資料夾 + 指定 child 規則

```yaml
version: 1

# 所有 YAML 檔案先套用
operations:
  - op: set
    path: $.common.timeout
    value: 30
    create_missing: true

rules:
  - id: child-a
    filters:
      path_allow: ["child-a/**"]
    operations:
      - op: replace
        path: $.server.host
        value: child-a-server

  - id: child-b-application
    filters:
      path_allow: ["child-b/application.yaml"]
    operations:
      - op: update_item
        path: $.services
        match: {name: api}
        set: {enabled: true}
```

```bat
python yaml_config_tool.py run-folder source rules.yaml output
```

XML 使用相同 rules 結構，入口改為 `xml_config_tool.py`，路徑改用 XML path。

## FAB / ENV 與外部變數表

```yaml
variable_map_file: variables/variable-map.yaml
```

```yaml
variable_map:
  FAB14:
    HOST: server-a
  FAB14:STAGING:
    HOST: server-a-staging
  FAB14-FZ1:STAGING:
    HOST: server-c
```

也能在執行時覆蓋：

```bat
python yaml_config_tool.py run-folder source config.yaml output --var HOST=temporary-host
```

## YAML 輸出格式與換行

預設等同：

```python
yaml.indent(mapping=2, sequence=4, offset=2)
```

可由 config 調整：

```yaml
options:
  yaml_output:
    mapping: 2
    sequence: 4
    offset: 2
    width: 4096
    preserve_quotes: true
    line_ending: preserve  # preserve / lf / crlf
```

XML 可用 `options.xml_output.line_ending`。未設定時保留來源換行。

## 安裝與平台

需求：Python 3.10 以上。

```bat
python -m pip install -r requirements.txt
```

Windows 可使用 `RUN_WINDOWS.cmd` / `RUN_XML_WINDOWS.cmd`；Linux 可使用 `RUN_LINUX.sh` / `RUN_XML_LINUX.sh`，也可直接以 `python` 或 `python3` 執行入口。

## 交付前自我驗證

```bat
python -m pip install -r requirements-dev.txt
RUN_TESTS_WINDOWS.cmd
```

Linux：

```bash
python3 -m pip install -r requirements-dev.txt
./RUN_TESTS_LINUX.sh
```

## 文件導覽

- [5 分鐘快速開始](docs/QUICK_START_zh-TW.md)
- [完整設定與 operation 參考](docs/CONFIG_REFERENCE_zh-TW.md)
- [CLI 指令參考](docs/CLI_REFERENCE_zh-TW.md)
- [XML 使用指南](docs/XML_USER_GUIDE_zh-TW.md)
- [多版本升級完整案例](docs/MULTI_VERSION_UPGRADE_zh-TW.md)
- [測試矩陣與驗收範圍](docs/TEST_MATRIX_zh-TW.md)
- [常見問題與排錯](docs/TROUBLESHOOTING_zh-TW.md)


## 進階手寫 Config

- [Section、List、Pattern 與 missing 完整範例](docs/SECTION_CONFIG_EXAMPLES_zh-TW.md)

# YAML / XML Config Tool

以 Python 搭配 YAML config，批次修改 YAML 或 XML，並盡量保留原本註解、順序、縮排與換行格式。

## 最常用：before / after 自動產生一份 config

YAML：

```bat
python yaml_config_tool.py compile-folder before-folder after-folder generated
python yaml_config_tool.py apply-folder before-folder generated output-folder
python yaml_config_tool.py verify-folder before-folder generated after-folder
```

XML：

```bat
python xml_config_tool.py compile-folder before-folder after-folder generated
python xml_config_tool.py apply-folder before-folder generated output-folder
python xml_config_tool.py verify-folder before-folder generated after-folder
```

預設輸出只有：

```text
generated/
└─ patch.yaml
```

日常只需閱讀、修改與版控 `patch.yaml`。

Compiler 會實際套用產生的 operations 並比對 after 的原始 bytes；若註解、位置、BOM、LF/CRLF 或特殊格式無法由最小 operations 精確重現，會在同一份 `patch.yaml` 中使用有標記的 exact-byte fallback。

需要逐檔 config 與稽核資訊時才使用：

```bat
python yaml_config_tool.py compile-folder before-folder after-folder generated --layout expanded
python xml_config_tool.py compile-folder before-folder after-folder generated --layout expanded
```

expanded 模式會額外產生 `manifest.yaml`、`configs/`，以及必要的 payload 內容；舊流程仍可正常使用。

## 搭配外部 variable map

`generated/patch.yaml`：

```yaml
version: 1
kind: yaml-folder-patch-compact
variable_map_file: variable-map.yaml

files:
  FAB14-FZ1/STAGING/app/application.yaml:
    ops:
      - set: [$.server.host, "{{ HOST }}"]
      - set: [$.server.timeout, "{{ TIMEOUT }}"]
```

`generated/variable-map.yaml`：

```yaml
variable_map:
  FAB14:
    HOST: fab14-default
    TIMEOUT: 30

  FAB14:STAGING:
    HOST: fab14-staging
    TIMEOUT: 45

  FAB14-FZ1:STAGING:
    HOST: fab14-fz1-staging
```

同樣支援 XML compact patch。完整可執行範例位於：

```text
examples/folder-compact-mapping/yaml/
examples/folder-compact-mapping/xml/
```

## 文件

- `docs/QUICK_START_zh-TW.md`：新手快速開始
- `docs/CONFIG_REFERENCE_zh-TW.md`：完整 config 欄位
- `docs/SECTION_CONFIG_EXAMPLES_zh-TW.md`：大型 section、list、dict 與位置操作
- `docs/CLI_REFERENCE_zh-TW.md`：CLI 指令
- `docs/XML_USER_GUIDE_zh-TW.md`：XML 使用方式
- `docs/TROUBLESHOOTING_zh-TW.md`：常見錯誤
- `docs/TEST_MATRIX_zh-TW.md`：完整驗收範圍
- `docs/LARGE_SCALE_VALIDATION_zh-TW.md`：上千行 YAML/XML、註解、位置與 exact-byte 壓力驗證

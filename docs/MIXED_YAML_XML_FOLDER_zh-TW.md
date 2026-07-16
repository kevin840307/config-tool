# YAML + XML 混合資料夾操作

## 適用情境

同一個資料夾樹中同時存在 YAML 與 XML，例如：

```text
before-folder/
├─ app/application.yaml
├─ app/application.xml
├─ appB/v506/application.yaml
└─ appB/v506/application.xml
```

## 一次自動產生 config

```bash
python config_tool.py compile-folder before-folder after-folder generated
```

輸出只有：

```text
generated/patch.yaml
```

工具會同時分析：

- YAML/YML value、dict、list、section、順序與註解差異
- XML element、attribute、重複節點、位置與註解差異
- YAML/XML 新增檔案
- YAML/XML 刪除檔案
- YAML/XML 修改檔案

## 一次產生結果

```bash
python config_tool.py apply-folder before-folder generated output-folder
```

`output-folder` 會保留其他未修改檔案，並同時套用 YAML 與 XML 變更。

## 完整驗證

```bash
python config_tool.py verify-folder before-folder generated after-folder
```

驗證方式是整棵目錄逐檔 byte 比較，不只是解析後的 value 比較。因此註解、位置、BOM、LF/CRLF 也會被驗證。

## patch.yaml 範例

```yaml
version: 1
kind: mixed-folder-patch-compact
formats: [yaml, xml]

files:
  app/application.yaml:
    format: yaml
    ops:
      - op: set
        path: $.runtime
        missing: create
        value:
          retry:
            count: 3
            delays: [1, 5, 15]

  app/application.xml:
    format: xml
    config:
      version: 1
      format: xml
      operations:
        - op: set
          path: /configuration/runtime/retry
          missing: create
          value:
            count: "3"
            delays:
              delay: ["1", "5", "15"]

  app/new.yaml:
    format: yaml
    create_text: |
      # new config
      feature:
        enabled: true
    text_options:
      encoding: utf-8
      bom: false
      line_ending: lf

  app/obsolete.xml:
    format: xml
    delete: true
```

## 與既有入口的關係

- 只處理 YAML：`yaml_config_tool.py`
- 只處理 XML：`xml_config_tool.py`
- YAML 與 XML 同時處理：`config_tool.py`

三個入口彼此獨立，新增 mixed folder 功能不會改變既有 YAML/XML 行為。

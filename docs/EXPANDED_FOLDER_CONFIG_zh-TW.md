# Folder 逐檔 Config 與 matched-files-only

## 逐檔產生 YAML/XML config

```bash
python config_tool.py compile-folder before after generated --layout expanded
```

`manifest.yaml` 只負責列出相對路徑、格式、action 與 config 路徑；實際修改規則放在各自的 config：

```text
generated/
├─ manifest.yaml
└─ configs/
   ├─ yaml/
   │  ├─ app/application.yaml.config.yaml
   │  └─ appB/v506/application.yaml.config.yaml
   └─ xml/
      ├─ app/application.xml.config.yaml
      └─ appB/v506/application.xml.config.yaml
```

YAML config 由 YAML 單檔 engine 執行，XML config 由 XML 單檔 engine 執行，因此行為與單檔 `apply` 一致，也較容易逐檔人工檢查與修改。

## 只產生共同路徑的 auto config

```bash
python config_tool.py compile-folder before after generated \
  --layout expanded \
  --matched-files-only
```

假設：

```text
before/                         after/
├─ app/application.yaml        ├─ app/application.yaml
├─ app/application.xml         ├─ app/application.xml
└─ old/legacy.yaml             └─ new/feature.yaml
```

只會為以下檔案生成 patch config：

```text
app/application.yaml
app/application.xml
```

不會生成：

```text
old/legacy.yaml  -> delete
new/feature.yaml -> create
```

這個選項不等於 path filter，也不會刪除來源中的 before-only 檔案。它只限制 compiler 要為哪些相對路徑產生自動 config。

## Compact 也可使用

```bash
python config_tool.py compile-folder before after generated --matched-files-only
```

此時仍只產生一份 `patch.yaml`，但其中不會有 create/delete entry。

## Compact 也採逐檔編譯後組合

Compact 與 expanded 現在使用完全相同的逐檔編譯流程：

```text
每個 YAML/XML 檔案
→ 呼叫對應的單檔 compiler
→ 產生完整單檔 config
→ 驗證單檔結果
→ compact 模式把各單檔 config 嵌入同一份 patch.yaml
```

因此 compact 並不是另一套簡化 compiler。兩種 layout 的差異只有包裝形式：

- `compact`：所有逐檔 config 組合在一份 `patch.yaml`
- `expanded`：每份 config 分別放在 `configs/yaml/` 或 `configs/xml/`

Compact 範例：

```yaml
files:
  app/application.yaml:
    format: yaml
    config:
      version: 1
      options:
        atomic_write: true
      operations:
        - op: set
          path: $.app.version
          value: v2

  app/application.xml:
    format: xml
    config:
      version: 1
      format: xml
      operations:
        - op: set
          path: /configuration/app/@version
          value: v2
```

這可避免 operations 在 folder compact 化時被再次轉換，也能讓單檔、compact folder、expanded folder 的行為保持一致。

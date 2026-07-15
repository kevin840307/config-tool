# Python API 使用手冊

v0.10.0-rc1 提供穩定的頂層 facade：

```python
from config_tool_api import ConfigTool

tool = ConfigTool()
```

使用者不需要直接依賴內部的 `YamlPatchEngine`、`XmlPatchEngine`、`FolderCompiler` 或 `MixedFolderCompiler`。

## 單檔 Auto Compile

```python
result = tool.compile(
    before="a.yaml",
    after="c.yaml",
    output="patch.yaml",
)

assert result.ok
print(result.strategy)
print(result.warnings)
```

格式會依副檔名自動判斷，也可明確指定：

```python
result = tool.compile("before.xml", "after.xml", "patch.yaml", format="xml")
```

### 搭配 Mapping 泛化

```python
result = tool.compile(
    "a.yaml",
    "c.yaml",
    "patch.yaml",
    variable_map_files=["mappings/system-a.yaml"],
    fab="FAB14-FZ1",
    env="STAGING",
    retry_protection=False,
)
```

只有 mapping 泛化後仍可 100% replay 才會接受，否則自動回退固定值 config。

## Apply

```python
result = tool.apply(
    source="a.yaml",
    config="patch.yaml",
    output="result.yaml",
    variables={"BUILD_NUMBER": 123},
    variable_map_files=["mappings/runtime.yaml"],
)

print(result.changed)
print(result.skipped_operations)
```

XML 使用相同介面：

```python
result = tool.apply("before.xml", "patch.yaml", "result.xml")
```

## Verify

```python
result = tool.verify(
    before="a.yaml",
    config="patch.yaml",
    expected="c.yaml",
)

assert result.verified
```

預設比較 YAML/XML 結構、值、型別、順序與支援的格式資訊。要求原始 bytes 完全一致時：

```python
result = tool.verify("a.yaml", "patch.yaml", "c.yaml", exact_bytes=True)
```

## Mixed YAML/XML Folder

```python
compile_result = tool.compile_folder(
    before_root="before",
    after_root="after",
    output_root="generated",
    format="mixed",
    layout="compact",
)

apply_result = tool.apply_folder(
    source_root="before",
    generated_root="generated",
    output_root="output",
    format="mixed",
)

verify_result = tool.verify_folder(
    source_root="before",
    generated_root="generated",
    expected_root="after",
    format="mixed",
)
```

僅 YAML 或 XML：

```python
tool.compile_folder("before", "after", "generated", format="yaml")
tool.compile_folder("before", "after", "generated", format="xml")
```

## Retry 防護與可讀模式

```python
tool = ConfigTool(
    retry_protection=False,  # 預設不加入 duplicate/retry 防護
    readable=True,           # 預設輸出人類可讀簡寫
)
```

也可在單次 compile 覆蓋：

```python
tool.compile(
    "a.yaml",
    "c.yaml",
    "patch.yaml",
    retry_protection=True,
    readable=True,
)
```

## 統一回傳格式

所有公開方法都回傳 `ConfigToolResult`：

```python
result.ok
result.action
result.format
result.output
result.changed
result.verified
result.strategy
result.warnings
result.skipped_operations
result.data
```

轉成可序列化 dict：

```python
payload = result.to_dict()
```

`Path`、dataclass、list 與 dict 會自動轉成 JSON-friendly 資料。

## Format 自動判斷

單檔：

- `.yaml`、`.yml` → YAML
- `.xml` → XML

無法由副檔名判斷時必須明確指定：

```python
tool.apply("config.data", "patch.yaml", format="yaml")
```

Folder 預設使用 `mixed`，同時處理 YAML 與 XML。

## 錯誤處理

設定錯誤、selector 錯誤、格式錯誤或無法安全還原時會拋出明確例外，不會只回傳 `ok=False` 後靜默忽略。

建議整合方式：

```python
try:
    result = tool.apply("a.yaml", "patch.yaml", "result.yaml")
except (ValueError, OSError) as exc:
    print(f"Config 處理失敗: {exc}")
```

核心 engine 的 `ConfigError`、`ValidationError` 仍可由進階使用者另外捕捉。

## Folder verify 使用 runtime mapping

```python
result = tool.verify_folder(
    source_root="source",
    generated_root="generated",
    expected_root="expected",
    format="yaml",
    variables={"BUILD": 123},
    variable_map_files=["common.yaml", "system-a.yaml"],
)
```

`verify_folder` 會在隔離目錄套用 patch，再與 expected folder 比對，不會修改 source folder。

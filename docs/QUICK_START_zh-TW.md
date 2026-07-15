# 快速開始

## 1. Auto 產生 patch

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml
```

## 2. 套用與驗證

```bash
python yaml_config_tool.py apply before.yaml patch.yaml -o result.yaml
python yaml_config_tool.py verify before.yaml patch.yaml after.yaml
```

## 3. 使用既有 mapping

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml \
  --variable-map-file values.yaml --fab FAB14-FZ1 --env STAGING
```

Compiler 會先產生精確 patch，再將唯一可對應值泛化成 `{{ variable }}`，最後以同一 mapping replay。失敗就保留固定值。

## 4. Quote 不需設定

Auto compiler 自動跟隨 after：

```yaml
plain: 2026.04.0
single: '2026.04.0'
double: "2026.04.0"
```

Mapping 泛化後仍會保留三種樣式。

人工指定時才使用：

```yaml
- op: set
  path: $/version
  value: '{{ version }}'
  quote: double
```

## 5. Retry 防護

預設不加入。需要同一 patch 可安全重跑：

```bash
python yaml_config_tool.py compile before.yaml after.yaml -o patch.yaml --retry-protection
```

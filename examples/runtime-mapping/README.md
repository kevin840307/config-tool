# 執行時傳入 mapping

patch.yaml 不必包含 variable_map_file：

```bash
python config_tool.py apply-folder source generated output \
  --variable-map-file mapping/common.yaml \
  --variable-map-file mapping/system-a.yaml \
  --var RELEASE_VERSION=2026.07
```

優先順序：patch mapping → runtime mapping（依參數順序）→ --var。

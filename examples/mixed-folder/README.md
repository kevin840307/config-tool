# 混合 YAML/XML folder 範例

```bash
python config_tool.py compile-folder examples/mixed-folder/before examples/mixed-folder/after examples/mixed-folder/generated
python config_tool.py apply-folder examples/mixed-folder/before examples/mixed-folder/generated examples/mixed-folder/output
python config_tool.py verify-folder examples/mixed-folder/before examples/mixed-folder/generated examples/mixed-folder/after
```

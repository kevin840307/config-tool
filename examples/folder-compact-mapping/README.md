# 單一 patch.yaml + variable-map.yaml

YAML：

```bat
python yaml_config_tool.py apply-folder examples\folder-compact-mapping\yaml\source examples\folder-compact-mapping\yaml\generated output-yaml
```

XML：

```bat
python xml_config_tool.py apply-folder examples\folder-compact-mapping\xml\source examples\folder-compact-mapping\xml\generated output-xml
```

`FAB14-FZ1/STAGING` 會先命中 `FAB14:STAGING`，再由更精確的 `FAB14-FZ1:STAGING` 覆蓋同名變數。

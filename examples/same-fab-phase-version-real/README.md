# Same FAB + ENV phase/version folder regression

This fixture models the real layout:

```text
ROOT/F13/STG/values.yaml
ROOT/F13/STG/config/appA/application.yaml
ROOT/F13/STG/config/appB/v507/application.yaml
ROOT/F13/STG/config/appB/v509/application.yaml
ROOT/F13/STG/config/appB/v510/application.yaml
```

The FAB (`f13` / `fab13`), ENV (`stg`) and namespace do **not** change. Only version lifecycle values are externalized through mapping:

- source: old=v507, current=v509, new=v510
- target: old=v601, current=v603, new=v604

Version directories are pre-created in before/after. The tool only transforms `values.yaml`, YAML and XML content.

Coverage:
- five Spring apps, versioned and unversioned config layouts
- phases p1-p6 plus FAB-specific phase f13p1
- old version removed from phase lists
- current version retained and shadow=false
- new version copied from current and adjusted
- common fields added to all retained/new versions
- phase-specific residuals
- deep dict/list K8s structures: HPA, containers, resources, affinity, topology spread, volumes, config, image, observability
- Auto compile path/paths/*/[*] consolidation
- mixed folder YAML/XML apply and exact result comparison

Generated values.yaml lines: 3519
Compile seconds in fixture generation: 12.889

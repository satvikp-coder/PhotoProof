# Test fixtures

`mockplugins/` contains **trusted test-only plugins**. They exercise the plugin
framework and perform no forensic analysis.

| Package | Purpose |
|---|---|
| `mockplugins.demo` | A valid graph: `source → consumer`, `observer` (ordering-only), `configurable` (behaviour selected via `configs/plugins/demo.configurable.yaml`), `comparison` (needs a reference image). |
| `mockplugins.broken` | Invalid version, incompatible API, missing entry module, import error. |
| `mockplugins.duplicate` | Two packages claiming the same plugin ID. |

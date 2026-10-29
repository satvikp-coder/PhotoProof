# Configuration overview

Each setting has exactly one home:

| File | Owns | Validated by |
|---|---|---|
| `app.toml` | Product identity, version, supported platforms, hardware requirements | `AppIdentity` |
| `settings.yaml` | Global application behaviour | `Settings` |
| `configs/profiles/<name>.yaml` | Analysis profiles: mode, plugin overrides, required plugins/outputs | `Profile` |
| `configs/plugins/<plugin_id>.yaml` | Algorithm parameters for one plugin | the plugin's `config_schema` |
| `.env` / environment | Two allowlisted overrides (below) | loader |

## Validation rules

- All files are parsed safely (TOML via `tomllib`, YAML via a `SafeLoader` subclass).
  YAML tags such as `!!python/object` are rejected, and **duplicate keys are errors**.
- **Unknown keys are errors** everywhere (strict policy). A typo never silently does
  nothing.
- Types, ranges and enums are checked. Errors name the file and the dotted key path.
- `settings.yaml` `schema_version` must equal `app.toml`
  `application.config_schema_version`.
- Security-relevant cross-field rules are enforced (see [settings](settings.md)).
- No invalid value is silently replaced by a default.

## Environment overrides

Only these variables are honoured. Any other `PHOTOPROOF_*` variable is an error.

| Variable | Overrides |
|---|---|
| `PHOTOPROOF_ENVIRONMENT` | `environment.mode` |
| `PHOTOPROOF_LOG_LEVEL` | `logging.level` |
| `PHOTOPROOF_LAUNCHER_VERBOSE` | (launcher only) print the launcher report on every start |

## Precedence by category

| Category | Precedence (highest first) |
|---|---|
| Plugin enablement | see [policy](policy.md) |
| `environment.mode`, `logging.level` | environment variable → `settings.yaml` |
| Plugin parameters | `configs/plugins/<id>.yaml` → plugin schema defaults |
| Everything else | `settings.yaml` only |

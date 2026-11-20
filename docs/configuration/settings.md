# settings.yaml reference

Every key is required unless noted. Unknown keys are rejected. Booleans (including
feature flags) must be real YAML booleans: `true`/`false`, not `"yes"`, `1` or
`"false"`. Configuration files larger than 1 MiB are rejected.

!!! note "Reserved sections"
    `services` and `ui` are validated now but consumed by planned
    components (ingestion, background workers, WebKit). They are documented here so
    that their meaning is fixed before the code arrives.

| Key | Default | Accepted values | Purpose and safety notes |
|---|---|---|---|
| `schema_version` | `1` | int ≥ 1 | Must equal `app.toml` `config_schema_version`. |
| `environment.mode` | `development` | `development` \| `testing` \| `production` | `production` enables stricter cross-checks (below). Env: `PHOTOPROOF_ENVIRONMENT`. |
| `runtime.root` | `ARE` | relative path, no `..` | Runtime data location under the repository. |
| `runtime.temp_max_age_hours` | `24` | int ≥ 1 | Temporary files and `.part` leftovers older than this are SAFE to delete. |
| `scheduling.executor` | `sequential` | `sequential` | Only implemented executor. |
| `scheduling.strict_priority_barriers` | `true` | bool | Finish each priority stage before the next (meaningful for future parallel executors). |
| `scheduling.default_plugin_timeout_seconds` | `60` | 0 < x ≤ 86400 | Used when a plugin declares no timeout. |
| `scheduling.random_seed` | `0` | int ≥ 0 | Passed to plugins; recorded in manifests and cache keys. |
| `dependencies.on_unavailable_requirement` | `skip` | `skip` \| `block` | When a `requires` dependency is disabled/unavailable: skip dependents, or block the run. |
| `failure_policy.on_required_failure` | `abort` | `abort` \| `degrade` | `abort` stops remaining plugins; `degrade` continues and marks the run `INCOMPLETE`. |
| `features.unknown_flags` | `error` | `error` \| `warn` | Handling of flags that are not known. |
| `features.flags` | `{}` | map of flag → bool | See [policy](policy.md). |
| `plugins.blacklist` | `[]` | unique plugin IDs | Never run; overrides flags and `required`. Bypass: `--ignore-blacklist` (CLI). |
| `plugins.allow_profile_overrides` | `true` | bool | Whether profiles may enable/disable plugins. |
| `profiles.default` | `standard` | profile file stem | Must exist in `configs/profiles/`. |
| `cache.enabled` | `true` | bool | Master switch for the run-level cache. |
| `logging.level` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | Env: `PHOTOPROOF_LOG_LEVEL`. Console shows WARNING+ unless DEBUG. |
| `logging.console_format` | `text` | `text` \| `json` | The log file is always JSON. |
| `logging.file_max_bytes` | `5000000` | ≥ 10000 | Rotation size for `ARE/logs/photoproof.log`. |
| `logging.file_backups` | `3` | 0–50 | Rotated files kept. |
| `health.preflight_fail_on` | `error` | `warning` \| `error` \| `critical` | Lowest severity that blocks startup. |
| `recovery.auto_repair_safe` | `true` | bool | Apply SAFE repairs during preflight. CONTROLLED repairs always need `--repair`. |
| `resources.max_input_bytes` | `104857600` | ≥ 1 | Inputs larger than this are rejected before being fully read. |
| `resources.max_image_pixels` | `178956970` | ≥ 1 | Decompression-bomb ceiling for future decoders (`DecodeLimits.max_pixels`). |
| `resources.max_artifact_read_bytes` | `67108864` | integer 1-2147483648 | Host ceiling per declared derived-artifact read; caller may request a smaller maximum. Bytes are size/hash verified before return. |
| `resources.min_free_disk_mb` | `1024` | ≥ 0 | Health check threshold for the runtime root. |
| `security.forbidden_plugins` | `[]` | unique plugin IDs | **Non-bypassable** denial. Not affected by `--ignore-blacklist`. |
| `security.allow_blacklist_bypass` | `true` | bool | Whether `--ignore-blacklist` is honoured at all. **Must be `false` in production.** |
| `formats.accepted` | `[jpeg, png, webp]` | subset of `jpeg png webp heif avif jxl tiff` | Only files whose signature matches one of these formats are accepted. Everything else is rejected at ingestion and audited. |
| `reporting.include_effective_settings` | `true` | bool | Embed effective settings in manifests. |
| `services.background_workers` | `0` | must be `0` | *Reserved*; workers are not implemented. |
| `retention.backup_days` | `null` | int ≥ 1 | Verified backups older than this many days produce a `database.backup` health warning. `null` disables the check. |
| `ui.host` | `127.0.0.1` | string | *Reserved* for WebKit. Must be loopback in production until authentication exists. |
| `ui.port` | `8501` | 1–65535 | *Reserved* for WebKit. |
| `development.debug` | `false` | bool | **Must be `false` in production.** |

## Cross-field rules

Validation fails when:

- `environment.mode: production` with `development.debug: true`;
- `environment.mode: production` with `security.allow_blacklist_bypass: true`;
- `environment.mode: production` with a non-loopback `ui.host`;
- a plugin is listed in both `plugins.blacklist` and `security.forbidden_plugins`.

## Migrating the schema

A change that renames, removes or changes the meaning of a key is a breaking
configuration change: bump `config_schema_version` in `app.toml`, bump
`schema_version` here, document the migration in `CHANGELOG.md`, and follow the MAJOR
version policy.


## Installed runtime home

`photoproof --root PATH COMMAND` selects the configuration/runtime home independently
of the installed Python package. An embedding caller's explicit root takes priority
over environment defaults; the CLI root takes priority over both. The allowlisted
`PHOTOPROOF_HOME` environment variable selects an absolute home when neither is set.
Other unknown `PHOTOPROOF_*` variables still fail validation. Checkout launches retain
the repository default; installed wheels use a platform user data directory.

Run `photoproof --root PATH init` to copy missing editable defaults. Existing files
and links are not overwritten. Without a local settings file or profiles directory,
installed mode reads packaged defaults; a malformed existing configuration fails.
Installed application identity always comes from the distribution app.toml; init
does not create an editable installed identity override.
`runtime.root` remains relative to this home, and normal evidence/permissions rules
still apply. Configuration never adds Python import paths. See
[ADR-026](../planning/adr-026-installed-wheel-runtime.md).

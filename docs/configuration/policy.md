# Feature flags, blacklist and profiles

## Plugin enablement precedence

`core/config/policy.py` decides each plugin and records **which rule decided it**
(`plugins` CLI command, manifests).

| # | Rule | Effect |
|---|---|---|
| 1 | `security.forbidden_plugins` | Disabled. Cannot be bypassed by anything. |
| 2 | Authorized `--ignore-blacklist` | Skips rule 3 only. |
| 3 | `plugins.blacklist` | Disabled. Overrides flags, profiles and `required: true`. |
| 4 | Feature flag `plugin.<id>` | Explicit enable/disable. |
| 5 | Profile `plugins.enable` / `plugins.disable` | Only if `plugins.allow_profile_overrides`. |
| 6 | Metadata `enabled_default` | Fallback. |

A bypass removes the blacklist; it does not force-enable a plugin that a flag or the
default disables.

!!! warning "Disabled required plugins block the run"
    If a required plugin is disabled at any level, the run is `BLOCKED` with a
    reason. It never produces an apparently complete analysis.

## `--ignore-blacklist`

- Accepted only from the **local CLI** (`OverrideOrigin.LOCAL_CLI`). Web and API
  origins raise `PolicyError`. WebKit must never expose it.
- Refused when `security.allow_blacklist_bypass: false` (mandatory in production).
- Does not bypass `forbidden_plugins`, input validation, size limits or any
  integrity check.
- Every use emits `POLICY_OVERRIDE` (written to `ARE/logs/audit.jsonl`), and the
  manifest records `blacklist_override: true` plus per-plugin decision sources.

## Feature flags

```yaml
features:
  unknown_flags: error
  flags:
    cache.run_level: false
    plugin.compression.ela: false
```

| Flag | Default | Meaning |
|---|---|---|
| `cache.run_level` | `true` | Reuse validated results of identical runs. |
| `events.progress` | `false` | Print plugin progress events in the CLI. |
| `plugin.<plugin_id>` | unset | Explicit plugin enable/disable (rule 4). Known once the plugin is registered. |

API: `app.flags.is_enabled("cache.run_level")`, `app.flags.plugin_override(id)`,
`app.flags.snapshot()` (recorded in every manifest). Flags are plain booleans; they
never evaluate expressions or import modules.

## Profiles

```yaml
# configs/profiles/standard.yaml
name: standard            # must match the file name
description: Single-image analysis using every enabled detector.
mode: single              # single | comparison
plugins:
  enable: []
  disable: []
required_plugins: []      # treated as required for this profile
required_outputs: []      # contracts that must be producible, else BLOCKED
```

Shipped profiles: `standard` (single image), `comparison` (target plus a
user-supplied reference, which is **not** assumed authentic), and `research` (for
experimental detectors). A comparison profile requires `--reference`; a single
profile rejects it.

Inspect the effective decisions:

```bash
python main.py plugins --profile standard
python main.py config --json
```

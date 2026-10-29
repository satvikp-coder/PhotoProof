# app.toml

The authoritative identity of the application. `version` here is the **only** place
the version is written (see [releases](../development/releases.md)).

| Key | Value | Notes |
|---|---|---|
| `application.name` | `PhotoProof` | Display name; `name.lower()` must equal `package`. |
| `application.version` | `0.1.0` | SemVer. Packaging derives the wheel version from this line. |
| `application.package` | `photoproof` | Import name; must exist as `src/photoproof/`. |
| `application.description` | text | |
| `application.release_channel` | `development` | `development` \| `preview` \| `stable` |
| `application.plugin_api_version` | `1.0.0` | Host plugin API. Bump on contract changes. |
| `application.config_schema_version` | `1` | Must match `settings.yaml` `schema_version`. |
| `[[developers]]` | 4 entries | `name`, `github`. Placeholders are marked; replace them. |
| `python.minimum` / `python.maximum` | `3.11` / `3.15` | Inclusive minor versions. |
| `platform.operating_systems` | `Linux`, `Darwin`, `Windows` | As reported by `platform.system()`. |
| `platform.architectures` | `x86_64`, `arm64` | `amd64`/`aarch64` are normalized. |
| `hardware.min_cpu_cores` / `recommended_cpu_cores` | 1 / 2 | Below minimum: refuse; below recommended: warn. |
| `hardware.min_memory_mb` / `recommended_memory_mb` | 2048 / 4096 | Total physical memory. Unknown is reported as unknown. |
| `hardware.min_free_disk_mb` | 1024 | Hard requirement. |
| `hardware.gpu` | `optional` | Informational; the base architecture never uses a GPU. |

## Identity validation

At startup both the launcher and `bootstrap` check that
`application.name.lower() == application.package` and that the package directory
is `src/<package>/` (`src` is the source-layout directory, not part of the import
name).

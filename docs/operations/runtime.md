# Runtime storage, cache, logs and manifests

## ARE — Application Runtime Environment

`ARE/` (configurable via `runtime.root`) holds generated data only and is ignored by
Git. Directories are created owner-only (0700) on POSIX.

| Directory | Contents |
|---|---|
| `objects/` | Reserved for original evidence, content-addressed (`<sha[:2]>/<sha>`), read-only |
| `artifacts/` | Plugin outputs, content-addressed, read-only |
| `cache/runs/` | Run-level cache entries (`<key>.json`) |
| `manifests/` | Execution manifests (`<run_id>.json`) |
| `database/` | `photoproof.db` (SQLite) and migration backups |
| `logs/` | `photoproof.log` (JSON lines, rotated), `audit.jsonl` |
| `tmp/` | Owner-only temporary files |
| `uploads/`, `quarantine/`, `reports/` | Reserved for ingestion and reporting (planned) |

All paths go through `safe_join`, which rejects absolute components, `..`, NUL bytes
and symlinks escaping the root. Writes are atomic (temp file in the same directory,
`fsync`, `os.replace`). An existing blob that fails verification is never overwritten.

## Cache

The run-level cache reuses a previous **COMPLETED** run only if the key matches and
the entry parses, has the right shape, and every referenced artifact still exists
with the right hash. Anything else is invalidated and recomputed. The key covers the
input and reference hashes, mode, profile, effective settings, feature flags, policy
decisions, plugin versions, implementation digests, plugin configs, declared
distribution versions, the pipeline fingerprint and the application version.

Bypass with `analyze --no-cache`, the `cache.run_level` flag or `cache.enabled: false`.
Failed, incomplete, aborted and blocked runs are never cached.

## Logs and events

Events (`APPLICATION_STARTED`, `RUN_CREATED`, `PLUGIN_STARTED`, `PLUGIN_FAILED`,
`ARTIFACT_CREATED`, `CACHE_HIT`, `HEALTH_CHECK_FAILED`, `REPAIR_APPLIED`,
`POLICY_OVERRIDE`, `RUN_COMPLETED`, …) carry UTC timestamps and typed fields. All are
logged to `photoproof.log`. `POLICY_OVERRIDE`, `REPAIR_APPLIED` and
`HEALTH_CHECK_FAILED` are also appended to `audit.jsonl`. A failing optional
subscriber is logged and ignored. A failing critical subscriber (state persistence,
audit) stops the operation.

## Execution manifests

Every run writes `ARE/manifests/<run_id>.json` with: run ID, input hashes, mode,
profile, application version, pipeline fingerprint, cache key and hit, effective
settings, feature flags, per-plugin policy decisions, blacklist override, plugin
versions and implementation digests, dependency graph, execution order, per-plugin
states/failures/timestamps/dependency status, blocked reasons, random seed, artifacts
with hashes, outputs and software versions.

`computational_fingerprint` hashes only reproducible state (no run ID, timestamps,
messages, tracebacks or software patch levels), so identical computations share a
fingerprint.

```bash
python main.py manifest <run_id>         # summary
python main.py manifest <run_id> --json  # full document
```

Historic replay and environment restoration are planned.

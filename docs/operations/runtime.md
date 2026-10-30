# Runtime storage, cache, logs and manifests

## ARE — Application Runtime Environment

`ARE/` (configurable via `runtime.root`) holds generated data only and is ignored by
Git. Directories are created owner-only (0700) on POSIX.

| Directory | Contents |
|---|---|
| `objects/` | Original evidence, content-addressed (`<sha[:2]>/<sha>`), read-only ([details](#evidence-store)) |
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

## Evidence store

`analyze` stores every input in `objects/` before any processing, and later consumers read
it through `EvidenceStore.open`, which re-hashes the file and raises `StorageError` on any
mismatch. No consumer receives unverified bytes.

- **One copy.** The storage key is the SHA-256 of the content, so identical bytes are stored
  once and each submission adds a record.
- **Never overwritten.** A new blob is published with a hard link, which fails if the target
  exists, so even a race between processes cannot write to an existing object. On filesystems
  without hard links (exFAT), it falls back to an atomic replace of identical content. A
  blob that fails verification stays in place for investigation.
- **Metadata only.** The submitted file name is stored in the database and never used to
  build a path. Non-UTF-8 name bytes are stored as visible escapes.
- **Rejected inputs.** Symlinks, directories, FIFOs and devices, empty files, and files over
  `resources.max_input_bytes`. Reading stops at the limit, and nothing is stored.
- **Interruptions.** The copy goes to `tmp/` first. A failure removes it, and a hard kill
  leaves a file for `runtime.stale_temp` to clean up. If the database write fails after the
  blob is stored, retrying records the submission against the same blob.

An unknown profile or an invalid reference combination is rejected before anything is
stored. Other rejections can leave an already accepted target in the store, because deletion
controls do not exist yet (#56).

### Database tables

Added in schema version 2. Existing databases are migrated by `python main.py --repair`,
which takes a backup first.

| Table | Columns |
|---|---|
| `evidence` | `sha256` (primary key), `size`, `first_seen_at` |
| `submissions` | `id`, `sha256`, `role`, `original_name`, `submitted_at`, `run_id` (null until the run finishes, or if it never did) |

## Cache

The run-level cache reuses a previous **COMPLETED** run only if the key matches and
the entry parses, has the right shape, and every referenced artifact still exists
with the right hash. Anything else is invalidated and recomputed. The key covers the
input and reference hashes, mode, profile, effective settings, feature flags, policy
decisions, plugin versions, implementation digests, plugin configs, declared
distribution versions, the pipeline fingerprint and the application version.

A cache hit records the reused plugin states and artifact references for the new run,
so the database is complete for cached runs too. Plugin configuration enters the key
as the SHA-256 of the raw file, so a malformed file fails only its plugin (exactly as
with the cache disabled).

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

`manifest` verifies the file against the SHA-256 recorded in the database when the run
finished, and refuses to display a manifest that was modified afterwards.

```bash
python main.py manifest <run_id>         # summary
python main.py manifest <run_id> --json  # full document
```

Historic replay and environment restoration are planned.

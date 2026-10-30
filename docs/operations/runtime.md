# Runtime storage, cache, logs and manifests

## ARE — Application Runtime Environment

`ARE/` (configurable via `runtime.root`) holds generated data only and is ignored by
Git. Directories are created owner-only (0700) on POSIX.
On supported local Windows volumes, the root and managed trees use protected
user/SYSTEM/Administrators ACLs, verified through native APIs. See the
[Windows security boundary](../security/index.md#windows-runtime-acl-boundary).

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

## Windows permissions and repair

`python main.py --doctor` reports broad, unsupported or unreadable ACLs under
`runtime.permissions`. On local ACL-capable volumes, SAFE repair replaces unexpected
directory/file ACEs with the documented SID policy and verifies the actual result.
`python main.py --repair --dry-run` previews actions; `python main.py --repair`
applies them. Normal preflight also attempts SAFE repair when enabled. Failed
Windows enforcement remains an error by default, never a healthy result.

The root and all ten listed trees, including nested directories/files, are checked.
Unknown entries directly under the root are outside managed permission repair; do not
store unrelated material there. No recursive ACL command, outside-root traversal or
evidence-byte rewrite occurs. Reparse points/junctions and hard-linked files are
refused; use ordinary private directories instead. An unapproved owner requires
operator-managed ownership migration. No elevation is attempted.

Windows originals/artifacts use read-only attributes **and** read-only DACLs while
allowing internal reads. SYSTEM/Administrators retain read access for backup/recovery
on blobs and full access on directories/mutable files. Authorized owners can still
change permissions explicitly; this is not immutable hardware storage. Existing
hashes are checked as before; corrupt evidence remains MANUAL and never overwritten.

UNC/network-mapped paths and volumes without persistent ACL support are unverified:
initialization/doctor warn, permissions are not declared healthy, and blob writes
fail before storing bytes. Logs/database confidentiality there is not guaranteed.
Use local NTFS/ReFS and inspect native operation/readback errors before retrying.
Windows CI tests real local DACLs and normal write denial. Domain-SID serialization
and unsupported-volume/network error branches are tested separately; no domain-joined
host or real SMB/FAT volume is certified by those simulated edge-case tests.

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

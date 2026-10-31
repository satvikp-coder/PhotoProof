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
| `quarantine/` | Inputs being validated, under random names. Emptied on success or rejection; `runtime.stale_temp` cleans anything older |
| `uploads/`, `reports/` | Reserved for WebKit uploads and reporting (planned) |

All paths go through `safe_join`, which rejects absolute components, `..`, NUL bytes
and symlinks escaping the root. Writes are atomic (temp file, `fsync`, then a rename). Blobs
are published without any possibility of overwriting: see the
[evidence store](#evidence-store).

## Ingestion

`IngestionService` is the one way untrusted files enter PhotoProof. `analyze` uses it for every
input, and WebKit will use it too.

1. **Stage** the stream in `quarantine/` under a random name, enforcing
   `resources.max_input_bytes` while reading. A claimed length is never trusted, and reading stops
   within one chunk (1 MiB) of the limit.
2. **Validate** the type from the file's content. Empty files, unknown formats and formats outside
   `formats.accepted` are rejected.
3. **Promote** to the evidence store, which hashes and verifies it.

Every rejection writes an `INPUT_REJECTED` entry to `audit.jsonl`, with the reason, size and detected
format. The file's content and submitted name are not logged. Nothing stays in `quarantine/` or
`objects/` after a rejection.

A slow sender can hold ingestion open, so WebKit must apply its own per-request timeout (#39).

## Evidence store

`analyze` stores every input in `objects/` before any processing, and later consumers read
it through `EvidenceStore.open`, which re-hashes the file and raises `StorageError` on any
mismatch. No consumer receives unverified bytes.

- **One copy.** The storage key is the SHA-256 of the content, so identical bytes are stored
  once and each submission adds a record.
- **Never overwritten.** A new blob is published with a hard link, which fails if the target
  exists, so even a race between processes cannot write to an existing object. A blob that
  fails verification stays in place for investigation.
- **Hard links are required on POSIX.** `runtime.root` cannot be on a filesystem without them
  (exFAT, for example). Storing fails with an error instead of risking an overwrite.
- **Metadata only.** The submitted file name is stored in the database and never used to
  build a path. Non-UTF-8 name bytes are stored as visible escapes.
- **Rejected inputs.** Symlinks, directories, FIFOs and devices, empty files, and files over
  `resources.max_input_bytes`. Reading stops at the limit, and nothing is stored.
- **Interruptions.** The copy goes to `tmp/` first. A failure removes it, and a hard kill
  leaves a file for `runtime.stale_temp` to clean up. If the database write fails after the
  blob is stored, retrying records the submission against the same blob.

An unknown profile or an invalid reference combination is rejected before anything is
stored. Other rejections can leave an already accepted target in the store, because deletion
requires the explicit controls described under [run deletion](#explicit-run-deletion).

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

## Housekeeping

```console
python main.py housekeeping
python main.py housekeeping --apply
```

The first command is a pure dry run: it bypasses preflight/startup repair and file logging,
opens the existing database read-only, and does not write audit events or initialize an absent
runtime. Missing/uncertain references return a nonzero result. Configure retention first; all
retention defaults retain indefinitely. Example output (synthetic identity abbreviated):

```text
Housekeeping: DRY RUN; no changes
Protected: shared/pending originals, references, audit trails, backups, unknown files
  cache <sha256>: 512 bytes; 31.00 days; expired disposable cache
Projected reclaimed: 512 bytes
```

`--apply` is explicit authorization of the currently displayed plan. The service independently
replans under a SQLite EXCLUSIVE reservation, checks file identity/hash again, and unlinks
specific approved files only. It never recursively removes directories or follows symlinks,
junctions or reparse points. Hard-linked deletion candidates and corrupt blobs are refused.
There is no background scheduler and no preflight housekeeping repair action.

Deletion order is expired cache, unreferenced old derived artifacts, then eligible completed-run
manifests/metadata. Cache files and rows must agree. Every stored manifest must match its
SQLite-recorded hash and artifact references; all retained/cache references protect blobs.
Missing, malformed, unregistered or inconsistent reference data stops planning. A hash mismatch
is an integrity problem, never a reason to discard data. Unknown files, temporary files, report
outputs, migration backups and audit history are outside automatic retention. Empty directories
are left intact. Quota pressure does not change these protections.

Run retention removes the approved manifest, artifact association rows, plugin execution rows
and run row. Submission rows are retained with `run_id` cleared, as are all original evidence
blobs and evidence records. A retained cache source, report directory or any unlinked submission postpones run retirement.
The last guard protects the CLI interval between analysis completion and submission linking;
abandoned unlinked submissions intentionally require operator investigation before retirement.
Shared artifacts stay protected until the last retained manifest/cache/DB reference is gone;
newly unreferenced artifacts require another plan. This deliberately favors preservation over
maximum reclamation in one invocation. Evidence deletion is a separate explicit operation or a positively configured evidence policy below.

### Audit receipts, rotation and interrupted cleanup

Every unlink has a durable `HOUSEKEEPING_APPLIED` intent before deletion, then an outcome
(`deleted` or `failed`). Receipts contain data class, safe content hash, fixed reason, size and
reclaimed bytes; no original filenames, GPS or source paths. Intent/failure records claim zero
reclamation. Audit failures are critical and return nonzero. A failure writing the outcome after
unlink cannot undo deletion; the durable intent remains evidence of an uncertain outcome,
which must be reconciled by the operator. Earlier successful removals and their metadata are
preserved when a later candidate fails.

Filesystem unlink and SQLite commit are not one atomic transaction. A process/power failure
between unlink and metadata commit may leave a missing-manifest or missing-cache record. The
next plan fails closed, protecting all remaining blobs. Preserve audit files and database
backups; compare intent/outcome hashes with the specific missing file and database associations.
Do not delete blobs or reconstruct forensic results to make the graph pass. An authorized
operator must reconcile the interrupted derived-data retirement or restore metadata from a
trusted backup before retrying. Automatic crash recovery of these ambiguous cases is not
implemented. Audit records are receipts, not a tamper-evident chain (#81).

Audit append/rotation share a bounded native OS lock. Rotation uses `logging.file_max_bytes`
and increasing `audit.jsonl.1`, `.2`, ... segment numbers. Segments never get renumbered or
removed automatically; `logging.file_backups` applies only to `photoproof.log`. A single record
larger than the threshold stays complete. Incomplete/invalid JSONL in the current segment
blocks append rather than silently truncating crash evidence. Audit history can itself exceed
the quota; preserve/export it according to an explicit operator policy, never housekeeping.

### Concurrency and platform boundary

Before #82 merges, cleanup requires an idle runtime: any unfinished database run blocks all
candidates, regardless of age. Housekeeping applications serialize through SQLite EXCLUSIVE
transactions. Run creation/status/cache metadata writes wait or fail with SQLite's bounded
busy timeout. Controller artifact publication now takes its own SQLite write reservation and
checks the run is still active; a timed-out plugin cannot publish after run completion.
An analysis that has not created its run record may still ingest original evidence concurrently;
evidence publication now holds a SQLite write reservation through submission commit, excluding deletion of a blob before its new reference is committed. This does not implement #82's shared runtime locks,
process ownership or stale-run recovery. Do not run repair, external tools or older PhotoProof
versions concurrently with housekeeping. Direct storage API callers must externally quiesce
writes; they do not implicitly participate in the controller's reservation protocol.

Supported coordination assumes a trusted local filesystem with reliable SQLite/native locks.
Network shares, mapped network drives and unreliable/removable filesystems are unsupported;
there is no cross-host guarantee. A changing/inaccessible quota scan reports ERROR rather than
healthy. Logical-byte usage is a point-in-time estimate, not allocated blocks or a disk reservation.
Another process can consume space immediately afterward.

On POSIX, unlink removes the directory entry without changing read-only blob modes. Windows
main does not include #80 ACL enforcement; this feature does not import it. A read-only Windows
derived blob that cannot be unlinked is retained and reported as a failure. No permissions on
retained evidence or derived files are broadened. An owner can always change their own files:
these safeguards do not defend against a hostile account already controlling the runtime.

## Explicit run deletion

```console
python main.py delete RUN_ID
python main.py delete RUN_ID --with-evidence
```

The command itself is explicit authorization; there is no interactive prompt, so it works in
scripts. Consequence warnings are printed to stderr **before** destructive work. Both forms
require an existing, current, readable database and a verified idle runtime; they skip preflight
and automatic repair. Invalid IDs, missing runs, uncertain references or incomplete operations
return nonzero. A matching completed receipt makes a repeated identical request an idempotent
no-op; a different request cannot escalate an earlier default deletion into evidence deletion.

Default deletion removes only the selected run's manifest, linked cache files/rows, known
report files, eligible unshared artifact blobs, artifact/plugin associations, submission rows
and run row. It preserves original blobs and their evidence records; originals may still use
space. Other runs, other submissions, backups and audit history remain. Filenames belonging
only to that run are removed from live submission rows. SQLite free pages may still contain old
bytes: row deletion is **not** secure database erasure.

`--with-evidence` additionally authorizes original removal only if its sole submission belongs
to the selected run and no other retained run uses it. Multiple submissions protect the original,
even when linked to one run. A shared/pending/ambiguous original refuses the **whole request**
before deletion; use the default form to remove run data while retaining that original.
All manifest input roles, sizes, evidence rows, submission associations and object hashes must
agree. Unregistered objects, missing submissions/manifests, corrupt hashes and active runs block
cleanup rather than being interpreted as unreferenced data.

Both paths reuse housekeeping's manifest/SQLite/cache validator. Retained cache artifacts and
other run associations protect shared blobs. Current report generation is not implemented on
this stacked base: deletion recognizes only `report.json`, `report.html` and `index.html` directly
under `reports/RUN_ID/`. Unknown names/subdirectories cause refusal; empty directories remain.
Retained or unknown report data conservatively protects candidate derived artifacts. New report
formats need an explicit ownership contract; no format from an unmerged PR is assumed.

### Evidence retention

Set `retention.evidence_days` to a positive integer to make **unreferenced** originals eligible
for housekeeping. `null` retains indefinitely. The recorded first-seen timestamp determines age,
not mtime; future timestamps delay eligibility. A dry run changes nothing. Only `--apply` plus
the policy can delete eligible evidence, and all run/submission references must be absent.
Any report data conservatively postpones original retention. Default explicit run deletion can
leave an unreferenced original for a later policy apply. Run-age housekeeping retains submission
records with cleared run links, as in #55; those unlinked records remain protected and need
operator investigation. Evidence retention never treats an ambiguous submission as stale.

### Receipts and interrupted operations

A privacy operation writes a `database/deletions/OPERATION_ID.json` journal before
its audit intent and first unlink (0600 files/0700 directory on POSIX; existing runtime permission
behavior on Windows). Journals contain opaque IDs, a plan hash, mode and phase,
not filenames, paths, EXIF or content. Each approved file also has durable intent/deleted audit
receipts containing its hash/class/size. Failures record a safe category; missing audit storage
or write failure cannot silently authorize success. Audit history/rotation segments are retained.

The SQLite EXCLUSIVE reservation spans validation, file removal and metadata commit. Original
publication holds an IMMEDIATE reservation from copying through submission commit, closing the
same-content concurrent-upload race. This is narrower than #82's future runtime lock/process
model: do not run repair, external writers or older application versions concurrently. Network
filesystems are unsupported; detectable Windows non-fixed volumes, standard Linux network/FUSE
mounts and macOS non-local mounts are refused. This detection does not certify every driver.
Read-only Windows files that cannot be unlinked are retained with an actionable failure; no
original's permissions are broadened. POSIX unlink changes the directory entry, not blob bytes.

There is **no atomic rollback across SQLite and files**. A failure after any unlink leaves a
pending journal; default deletion and housekeeping refuse further automatic work. Even if all
files disappeared and SQLite committed, a missing final audit receipt keeps the operation
pending. Failure provably before any removal is cancelled and can be retried safely.

For recovery: stop all runtime writers; preserve the database, all journals, current/rotated audit
segments and backups; compare the pending operation ID and per-file intent/outcome hashes with
remaining files and database associations. Restore a complete consistent state from a trusted
backup or reconcile exactly the authorized rows/files under an independently reviewed procedure.
Do not delete journals to force a retry or infer successful erasure from missing files. There is
no automatic recovery/acknowledgment command in this issue. Once reconciled, an authorized owner
can finalize the journal phase only with verified receipt/database evidence. Audit receipts are
not a hash chain; stronger tamper detection is future #81.

Removing an original prevents replay/independent verification requiring those bytes; derived
reports may cease to be reproducible. Backups, exported reports, other externally supplied copies,
audit hashes and SQLite free pages may remain. Normal unlink is not guaranteed secure physical
media erasure, nor does it revoke existing open handles. WebKit upload/history controls (#39/#44)
must call these services and present the same privacy consequences; they are not implemented here.

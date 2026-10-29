---
id: PP-001
title: "feat(storage): preserve original evidence immutably with integrity hashing"
milestone: M1
labels: [type:feature, priority:P1, area:storage, area:security, effort:medium, status:ready]
depends_on: []
---
## Summary

Add an evidence store that copies every submitted original byte-for-byte into
`ARE/objects/`, identified by SHA-256, read-only, with a persistent evidence record
and verification on every read.

## Problem Statement

Today `analyze` only hashes the input file in place (`core/security/inputs.py`). Nothing
guarantees that the bytes analysed later, or shown in a report, are the bytes the user
submitted. The `objects/` directory and the `storage.integrity` health check exist, but
nothing writes originals into the store.

## Objective

Every analysis input is persisted once, immutably, before any processing. Every later
consumer reads it through a verifying API that fails loudly on corruption.

## Technical Background

Content addressing (as in Git) makes identity and integrity the same property: the
storage key is the SHA-256 of the content, so any modification is detectable by
re-hashing. `RuntimeStorage.put_blob("objects", data)` already implements atomic,
read-only (0400) and idempotent writes and refuses to overwrite a corrupt blob.

## Functional Requirements

- `EvidenceStore.ingest(path | bytes, *, original_name, role) -> EvidenceRecord` streams
  the input (respecting `resources.max_input_bytes`), hashes it, and stores it under
  `objects/<sha[:2]>/<sha>`.
- Re-ingesting identical bytes returns the existing record (deduplication) and records
  an additional *submission* (who/when/name), never a second copy.
- `EvidenceStore.open(sha256) -> BinaryIO` verifies the hash before returning data and
  raises `StorageError` on mismatch.
- The original file name is stored as metadata only. It is never used to build a path.
- Originals are never modified, re-encoded, renamed or deleted by this component.

## Technical Implementation

- New module `src/photoproof/core/storage/evidence.py`.
- Database migration 2: `evidence(sha256 PK, size, first_seen_at)` and
  `submissions(id, sha256 FK, role, original_name, submitted_at, run_id NULL)`.
  Use `Database` methods only; SQL stays in `core/database/store.py`.
- Streaming ingest: copy into `tmp/` with incremental hashing, then `os.replace` into
  the content-addressed location (same filesystem). Use the existing `atomic_write`
  semantics. Do not load very large files fully into memory.
- `InputDescriptor` gains nothing new; the controller obtains descriptors from
  `EvidenceRecord`.
- Error handling: oversize → `InputRejectedError`; disk full → `StorageError` with a
  clear message and no partial blob; hash mismatch on read → `StorageError`.

## Integration Requirements

- `cli analyze` calls `EvidenceStore.ingest` instead of `hash_input_file`.
- Manifests already record input hashes. Also link `submissions.run_id`.
- `storage.integrity` keeps treating `objects/` mismatches as CRITICAL/MANUAL.
- PP-011 (upload handling) builds on this API.

## Security and Edge Cases

- Symlinks and non-regular files are rejected (reuse `hash_input_file` semantics).
- Zero-byte files are rejected with an actionable message.
- Concurrent ingests of identical content must not corrupt the blob (the atomic
  replace of identical content is safe; add a test).
- Interrupted ingest leaves only a `tmp/` file that `runtime.stale_temp` cleans.
- The file name may contain path separators or control characters: store as-is
  (escaped on output), never interpret it.

## Testing Requirements

- Unit: dedup, verification failure, oversize rejection, zero-byte rejection, interrupted
  ingest (simulated `OSError` mid-copy), name with `../`.
- Integration: `analyze` stores the original and the manifest hash equals the stored blob.
- Resilience: tamper with a stored original → `open` raises and `doctor` reports CRITICAL.

## Acceptance Criteria

- [ ] Analysing a file stores exactly one read-only copy under `ARE/objects/`.
- [ ] Analysing the same bytes twice produces one blob and two submission records.
- [ ] Reading a tampered original raises `StorageError`, and no consumer receives the bytes.
- [ ] No code path writes to an existing object.
- [ ] Database migration 2 is applied with a backup by `--repair`, as documented.
- [ ] `docs/operations/runtime.md` documents the evidence store.

## Expected Outcome

Originals are preserved and verifiable. Every later issue (decoding, previews, reports)
reads evidence through one verifying API.

## Documentation Requirements

Update `docs/operations/runtime.md`, `docs/security/index.md` (evidence preservation)
and the database schema notes.

## Dependencies

None. This is the first ingestion building block.

## Priority and Effort

P1, medium. Every detector and report depends on trustworthy originals.

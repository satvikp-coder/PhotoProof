---
id: PP-081
title: "security(core): make the audit trail and manifests tamper-evident"
milestone: M6
labels: [type:security, priority:P2, area:security, area:core, effort:medium, status:ready]
depends_on: []
---
## Summary

Chain every audit entry to the previous one by hash, and anchor manifest hashes in that
chain. Then edits to `audit.jsonl`, manifests or the database's recorded hashes are
detectable by `doctor`.

## Problem Statement

Found in the code review: `audit.jsonl` is an ordinary append-only file, so entries can
be edited or deleted silently. The `manifest` command now verifies manifests against
the SHA-256 stored in SQLite, but anyone who can edit the manifest can also edit the
database row. For forensic use, the provenance of a result must be verifiable.

## Objective

A hash-chained audit log (each record includes `prev_sha256`), a `RUN_COMPLETED` audit
entry carrying the manifest hash, and a `doctor` check that verifies the chain and the
manifests end to end.

## Technical Background

Hash chains detect modification and truncation in the middle (not tail truncation
without an external anchor). For stronger guarantees, an optional periodic signed
checkpoint (Ed25519 key outside `ARE/`) or an external timestamping service. Document
these as options only.

## Functional Requirements

- Audit records: `seq`, `prev_sha256`, `sha256` of the canonical record.
- `RUN_COMPLETED` and `RUN_ABORTED` become audited events carrying `manifest_sha256`.
- Health check `audit.integrity`: verifies the chain and that each manifest hash in the
  chain matches both the database and the file (CRITICAL, MANUAL on mismatch).
- Rotation (PP-055) must preserve the chain across files.

## Technical Implementation

`core/logging/setup.py` (audit writer keeps the last hash; recovers it from the file
tail at start); `core/health/checks.py`.

## Integration Requirements

Manifest verification in `cli.py` additionally checks the audit chain.

## Security and Edge Cases

Concurrent writers (file lock or a single-writer rule). Crash mid-write (a partial last
line is detected and reported, never "repaired" silently).

## Testing Requirements

Edit, delete and reorder audit lines → detected. Edit a manifest and its database row
together → detected through the chain.

## Acceptance Criteria

- [ ] Any modification, deletion or reordering of audit entries is detected by `doctor` (tests).
- [ ] A manifest edited together with its database row is detected (test).
- [ ] The limits of tail truncation are documented.

## Expected Outcome

Verifiable provenance for every recorded result.

## Documentation Requirements

Security model (evidence preservation), operations runtime docs.

## Dependencies

None.

## Priority and Effort

P2, medium.

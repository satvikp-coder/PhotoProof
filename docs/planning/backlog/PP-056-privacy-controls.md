---
id: PP-056
title: "security(storage): add uploaded-image privacy and deletion controls"
milestone: M6
labels: [type:security, priority:P1, area:security, area:storage, effort:medium, status:blocked]
depends_on: [PP-001, PP-055]
---
## Summary

Give users explicit control over their uploaded images and derived data: delete a run
and its evidence, configure evidence retention, and keep sensitive metadata (GPS,
original file names) out of logs and exports.

## Problem Statement

Uploaded photographs can be highly personal. PhotoProof stores originals, previews,
manifests (which include file names) and logs, and offers no way to remove them.

## Objective

`photoproof delete RUN_ID [--with-evidence]`, an evidence retention policy, and a log
redaction policy, all audited, with the privacy behaviour documented.

## Technical Background

Data-minimization principles: store only what is needed, keep it no longer than needed,
and let users erase it. The trade-off is that deleting evidence makes replay (PP-054) and
re-verification impossible, so the UI and CLI must state that consequence.

## Functional Requirements

- Delete a run: its manifest, report, run-specific artifacts (reference-counted),
  database rows, and, with `--with-evidence`, the original blob if no other run
  references it.
- `retention.evidence_days` (default null = keep), applied by housekeeping (PP-055).
- Log redaction: original file names are hashed in logs, and GPS is never logged
  (enforced by a logging filter).
- The deletion receipt records what was removed (hashes only).

## Technical Implementation

A deletion service in `core/storage/`, reusing housekeeping's reference counting. A
logging `Filter` in `core/logging/setup.py`.

## Integration Requirements

The WebKit History delete action (PP-044). The privacy notice on the upload pages
(PP-039).

## Security and Edge Cases

Deleting evidence shared by several runs (refuse, or delete only the references).
Deletion while a job is running. Backups (PP-062) may still contain the data, which must
be documented.

## Testing Requirements

Deleting a run removes exactly its data (filesystem and DB assertions). Shared evidence
is protected. The log filter redacts file names (test with a captured handler).

## Acceptance Criteria

- [ ] `delete` removes run data and, if requested and unshared, the original (tests).
- [ ] Original file names never appear in logs in clear text (test).
- [ ] The privacy behaviour, including backups, is documented in `docs/security/index.md`.
- [ ] Every deletion is audited with hashes only.

## Expected Outcome

Users can trust PhotoProof with sensitive images.

## Documentation Requirements

Security model (privacy section), user guide.

## Dependencies

PP-001, PP-055.

## Priority and Effort

P1, medium. A privacy obligation before any shared deployment.

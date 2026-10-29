---
id: PP-062
title: "test(database): validate database backup and recovery"
milestone: M6
labels: [type:testing, priority:P2, area:database, area:testing, effort:medium, status:ready]
depends_on: []
---
## Summary

Add verified backups for the SQLite database (`photoproof backup`), a restore procedure
that only accepts verified backups, and tests for corruption and recovery scenarios.

## Problem Statement

Migrations already write a pre-migration backup, but there is no scheduled or manual
backup, no verification, and no tested restore path. The repair rules forbid restoring
unverified backups, yet there is no way to verify one.

## Objective

Backups with a manifest (SHA-256, schema version, row counts, `PRAGMA integrity_check`
result) and a CONTROLLED restore that refuses unverified or incompatible backups.

## Technical Background

The SQLite online backup API (`Connection.backup`) produces consistent snapshots while
the database is in use. The verification step: open the backup read-only, run
`integrity_check` and `foreign_key_check`, compare the schema version and record the
hash.

## Functional Requirements

- `photoproof backup` → `ARE/database/backups/<timestamp>.db` + `.json` manifest.
- `photoproof restore BACKUP [--dry-run]`: verify, then back up the current DB, then swap
  atomically. The current DB is never discarded.
- Health check: the newest verified backup's age (WARNING if older than a configurable
  number of days, when backups are enabled).
- `MANUAL` repair guidance in `database.integrity` points to `restore`.

## Technical Implementation

Extend `core/database/store.py` (backup and verify) and the CLI. The restore is a
CONTROLLED repair action.

## Integration Requirements

Housekeeping (PP-055) prunes old backups according to retention. Privacy (PP-056)
documents that backups contain run history.

## Security and Edge Cases

Restoring a backup from a newer schema → refuse. A backup file tampered with after
creation → hash mismatch → refuse. Concurrent writes during backup (the backup API
handles it; test).

## Testing Requirements

Corrupt the live DB (write garbage pages) → doctor CRITICAL → restore from the verified
backup → doctor OK. A tampered backup is rejected. A restore dry run changes nothing.

## Acceptance Criteria

- [ ] Backups are verified and described by a manifest (test).
- [ ] Restore refuses unverified, tampered or incompatible backups (tests).
- [ ] The corrupt → restore → healthy scenario passes end to end.
- [ ] The current database is preserved before every restore.

## Expected Outcome

Recoverable persistent state, with no unverified restores.

## Documentation Requirements

Operations health and repair docs (backup and restore section).

## Dependencies

None.

## Priority and Effort

P2, medium.

---
id: PP-044
title: "feat(WebKit): browse analysis history and stored results"
milestone: M5
labels: [type:feature, priority:P2, area:WebKit, area:database, effort:medium, status:blocked]
depends_on: [PP-039]
---
## Summary

A History page listing past runs (date, profile, status, assessment, cache hit, input
hash prefix), with filtering, pagination and links to stored results, and a delete
action wired to the privacy controls (PP-056).

## Problem Statement

Runs are persisted in SQLite, but the only way to find them is the CLI with a run ID.

## Objective

A paginated, filterable history backed by indexed database queries.

## Technical Background

Keyset pagination (`WHERE created_at < ? ORDER BY created_at DESC LIMIT ?`) scales better
than OFFSET. An index on `runs(created_at)` is needed (migration).

## Functional Requirements

- Columns: created time (local timezone), profile, run status, assessment category,
  cached, input hash prefix, original file name (escaped).
- Filters: status, profile, date range.
- Row actions: open result, view manifest (PP-048), delete (PP-056).

## Technical Implementation

`Database.list_runs(filters, cursor, limit)` in `core/database/store.py`, plus a
migration adding indexes. `WebKit/pages/history.*`.

## Integration Requirements

Uses the submission records from PP-001 for the file names.

## Security and Edge Cases

Single-user assumption (document it; no multi-user authorization exists). Escape file
names. Runs whose manifest is missing → show the integrity warning and link to the
doctor.

## Testing Requirements

Database pagination and filter tests; page rendering tests.

## Acceptance Criteria

- [ ] History lists runs with keyset pagination and filters (tests).
- [ ] Indexes added through a migration with backup.
- [ ] Runs with missing manifests are flagged, not hidden.

## Expected Outcome

Users can find and revisit past analyses.

## Documentation Requirements

The user guide, and the database schema notes.

## Dependencies

PP-039.

## Priority and Effort

P2, medium.

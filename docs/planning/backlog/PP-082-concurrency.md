---
id: PP-082
title: "test(resilience): verify concurrent processes sharing one runtime environment"
milestone: M6
labels: [type:testing, priority:P2, area:testing, area:storage, area:database, effort:medium, status:ready]
depends_on: []
---
## Summary

Test, and fix where needed, the behaviour of several PhotoProof processes (two
analyses, an analysis plus `doctor`/`repair`, later WebKit plus CLI) running at once
against the same `ARE/`.

## Problem Statement

Found in the code review: all concurrency reasoning so far assumes one process.
Untested interactions include:

- simultaneous writes to the same content-addressed blob;
- SQLite lock contention (no explicit `busy_timeout`);
- `repair` invalidating cache entries, or deleting stale temp files, while an
  analysis is using them;
- the `database.stale_runs` repair marking another process's long-running run as
  aborted (it only uses a one-hour age threshold);
- `audit.jsonl` appends interleaving.

## Objective

A documented concurrency model and a test suite that runs these scenarios with real
subprocesses.

## Technical Background

SQLite allows one writer at a time: set `PRAGMA busy_timeout`, and consider WAL (see
PP-050). Atomic `os.replace` of identical content is safe. Repairs need a lock file (an
advisory `ARE/.lock` taken by `repair`) or must exclude live runs (record the owning
PID and host in `runs`).

## Functional Requirements

- `runs` records `pid` and `hostname`. `database.stale_runs` excludes runs whose
  process is still alive.
- `repair` takes an exclusive advisory lock; analyses take a shared one (cross-platform
  helper).
- `busy_timeout` is set explicitly.
- The documented model lists what may run concurrently.

## Technical Implementation

`core/database/store.py` (migration adding the columns; busy timeout), a lock helper in
`core/storage/`, and `tests/resilience/test_concurrency.py` using `multiprocessing`.

## Integration Requirements

WebKit (PP-038 onwards) and the workers (PP-051) depend on this model.

## Security and Edge Cases

Stale lock files after a crash (store the PID and verify it). Network filesystems
(locks unreliable; document them as unsupported).

## Testing Requirements

Subprocess tests: two analyses of the same input, and an analysis during `repair`. No
corruption, no failed writes, and no live run marked aborted.

## Acceptance Criteria

- [ ] Two concurrent analyses of the same input both complete (test).
- [ ] `repair` never marks a live run aborted, and never deletes files in use (tests).
- [ ] Concurrency model documented.

## Expected Outcome

Safe multi-process operation, a prerequisite for the web UI and workers.

## Documentation Requirements

Architecture (concurrency model), operations docs.

## Dependencies

None.

## Priority and Effort

P2, medium.

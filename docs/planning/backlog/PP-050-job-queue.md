---
id: PP-050
title: "feat(core): add a durable SQLite-backed background job queue"
milestone: M6
labels: [type:feature, priority:P2, area:core, area:database, effort:large, status:needs-design]
depends_on: []
---
## Summary

Introduce `core/services/jobs.py`: a durable job queue stored in the existing SQLite
database, so WebKit can submit analyses asynchronously and survive restarts.

## Problem Statement

Analyses currently run synchronously in the caller's process. A web request would block
for the whole pipeline, and a crash loses the request.

## Objective

`JobQueue.submit(request) -> job_id`, `claim(worker_id) -> Job | None`, `complete`,
`fail` and `requeue_stale`, with at-least-once execution and idempotent completion.

## Technical Background

SQLite in WAL mode supports one writer and many readers, which is enough at this scale.
Claim atomically with `UPDATE jobs SET state='running', worker=?, lease_until=? WHERE id =
(SELECT id FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1) RETURNING *`
(SQLite ≥ 3.35). Leases with expiry handle crashed workers. Do not add Redis, Celery or a
message broker: the scale does not justify them.

## Functional Requirements

- Job states: `queued`, `running`, `succeeded`, `failed`, `cancelled`; `attempts`,
  `max_attempts` (default 1 for analyses: forensic runs are not blindly retried),
  `lease_until`.
- The job payload is the validated `AnalysisRequest` JSON (descriptors only, never bytes).
- Stale leases are re-queued, or failed after `max_attempts`.
- Settings: `services.background_workers` becomes > 0 capable (schema change) and
  `jobs.lease_seconds` is added.

## Technical Implementation

Migration adding a `jobs` table and indexes. Enable WAL (`PRAGMA journal_mode=WAL`) with
a documented rationale and backup implications. SQL lives in `core/database/store.py`.

## Integration Requirements

WebKit (PP-039/PP-041) submits jobs. Workers (PP-051) claim them. The CLI `analyze`
remains synchronous.

## Security and Edge Cases

Duplicate submission (idempotency key = request hash plus a client token). Database lock
contention (busy timeout). Clock skew is irrelevant (single host), so document the
single-host assumption.

## Testing Requirements

Concurrency test: two claimers never get the same job. Stale lease recovery. A
crash-simulation test (kill a worker mid-job).

## Acceptance Criteria

- [ ] No job is ever claimed by two workers concurrently (stress test with threads/processes).
- [ ] Expired leases are recovered according to `max_attempts`.
- [ ] Queue survives an application restart (test).
- [ ] The WAL decision and its backup implications are documented.

## Expected Outcome

Asynchronous, durable analysis execution without new infrastructure.

## Documentation Requirements

An ADR, the architecture overview, and the settings reference.

## Dependencies

None.

## Priority and Effort

P2, large. Design review needed (status:needs-design).

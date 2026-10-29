---
id: PP-051
title: "feat(core): supervise worker processes that execute queued jobs"
milestone: M6
labels: [type:feature, priority:P2, area:core, effort:large, status:blocked]
depends_on: [PP-050]
---
## Summary

Add `core/services/manager.py` and `worker.py`. A service manager starts N worker
processes, monitors their heartbeats, restarts crashed workers with backoff, and shuts
down gracefully.

## Problem Statement

A queue needs consumers that keep running, recover from crashes and do not leave orphan
processes.

## Objective

`python main.py serve` runs WebKit plus the supervised workers. Each worker claims jobs
and runs the controller.

## Technical Background

The supervisor pattern: parent process, `multiprocessing` spawn children, heartbeats via
a database column or pipe, exponential backoff with a cap, and SIGTERM/CTRL_BREAK
handling for graceful drain (finish the current job or release its lease).

## Functional Requirements

- `services.background_workers` (≥ 1 when serving) and `services.restart_backoff_max_seconds`.
- Worker heartbeat every N seconds. A missed heartbeat → kill and restart, and the lease
  expires so the job is re-queued per policy.
- Graceful shutdown: stop claiming and wait up to a timeout.
- A health check `services.workers` reporting live workers vs configured.

## Technical Implementation

`src/photoproof/core/services/{manager,worker}.py`. Workers run `bootstrap()`
independently (no shared state). Logging per worker with `worker_id`.

## Integration Requirements

Uses PP-050. Optional: the process-isolated executor (PP-052) inside workers.

## Security and Edge Cases

Windows signal semantics (no SIGTERM to children; use events). Fork safety (always
spawn). Zombie reaping. Runaway restart loops (backoff cap plus a CRITICAL health
finding).

## Testing Requirements

Kill a worker → it is restarted, and its job is re-queued or failed per policy. Graceful
shutdown test. Test on all three OSes in CI.

## Acceptance Criteria

- [ ] Crashed workers are restarted with backoff (test).
- [ ] Graceful shutdown leaves no orphan processes on Linux, macOS and Windows.
- [ ] The health check reports worker liveness.

## Expected Outcome

Reliable background execution.

## Documentation Requirements

Operations docs (`serve`, workers) and settings reference.

## Dependencies

PP-050.

## Priority and Effort

P2, large.

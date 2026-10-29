---
id: PP-073
title: "feat(core): add a shared child-process runner with resource limits"
milestone: M1
labels: [type:security, priority:P1, area:security, area:core, effort:medium, status:ready]
depends_on: []
---
## Summary

Build one small, well-tested helper that runs a Python callable in a spawned child
process with wall-clock, CPU and memory limits and returns its result or a typed failure.
The isolated decoder (PP-009) and the process executor (PP-052) both build on it.

## Problem Statement

PP-009 and PP-052 each need the same hard part: reliable child-process limits on Linux, macOS
and Windows, with crash detection and cleanup. Building it twice wastes effort and
produces two different security behaviours. It also does not need a decoder or a
plugin to be developed, so it can start now.

## Objective

`photoproof.core.isolation.run_isolated(func, payload, *, limits) -> IsolatedResult`,
where `IsolatedResult` is either `Ok(value)` or `Failure(kind, detail)` with
`kind ∈ {timeout, memory_limit, cpu_limit, crashed, raised}`.

## Technical Background

- `multiprocessing.get_context("spawn")` on every OS (no fork with threads).
- POSIX: `resource.setrlimit(RLIMIT_AS / RLIMIT_DATA, RLIMIT_CPU)` in the child
  initializer. macOS ignores RLIMIT_AS, so document the achievable limit (RLIMIT_DATA or
  a polling RSS watchdog).
- Windows: a Job Object with `JOB_OBJECT_LIMIT_PROCESS_MEMORY` via `ctypes`, assigned
  right after spawn.
- Wall clock: parent `join(timeout)`, then `terminate()`, then `kill()` after a grace
  period.
- Results go through a `multiprocessing.Pipe`. Large binary payloads use
  `shared_memory`, unlinked in `finally`.

## Functional Requirements

- `ProcessLimits(timeout_s, memory_mb | None, cpu_s | None)`.
- Typed failure mapping from exit codes and signals (e.g. `-SIGSEGV` → `crashed`).
- Payload and result size caps, so a child cannot exhaust parent memory.
- A clean environment and working directory for the child (basic hardening; PP-059 adds
  more).
- The capability report (which limits are enforceable on this platform) is used by
  `doctor`.

## Technical Implementation

`src/photoproof/core/isolation.py`. Standard library only (plus `ctypes` on Windows).

## Integration Requirements

PP-009 wraps decoders with it; PP-052 wraps plugin invocations. Neither should reimplement the
process handling.

## Security and Edge Cases

Orphans and zombies (always join). Shared-memory leaks on crash. A child that ignores
SIGTERM. A child that floods the pipe (size cap). Pickling: only trusted code crosses the
boundary; untrusted data is bytes or arrays.

## Testing Requirements

Fixture callables that segfault (`ctypes.string_at(0)`), loop forever, allocate without
bound, raise, and return oversized results. Run them on all three OSes in CI, with
per-platform skips that state the reason.

## Acceptance Criteria

- [ ] Each failure kind is detected and reported correctly on Linux, macOS and Windows (or documented as unenforceable, with a skip reason).
- [ ] No orphan processes or leaked shared memory after the failure tests.
- [ ] The capability report is exposed for `doctor`.
- [ ] The per-call overhead is measured and documented.

## Expected Outcome

One trusted building block for every isolation feature.

## Documentation Requirements

Security model (runtime isolation plan), architecture components table.

## Dependencies

None.

## Priority and Effort

P1, medium.

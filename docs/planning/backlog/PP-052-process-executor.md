---
id: PP-052
title: "feat(pipeline): add a process-isolated executor for native-code crash isolation"
milestone: M6
labels: [type:security, priority:P1, area:pipeline, area:security, effort:large, status:blocked]
depends_on: [PP-009]
---
## Summary

Implement `ProcessExecutor`, a second `Executor` implementation that runs each plugin
invocation in a child process with resource limits. Plugin crashes, hangs and
memory blow-ups then cannot affect the application, and timeouts actually stop the
plugin.

## Problem Statement

`SequentialExecutor` runs plugins in-process on a daemon thread. A segfault in OpenCV or
PyWavelets kills the whole run, and a timed-out plugin keeps running in the background
(documented ponytail limitation in `executor.py`).

## Objective

`scheduling.executor: process` runs the same plans with identical semantics (states,
events, dependency handling) but with process isolation.

## Technical Background

Reuse the child-process and limits helper from PP-009. Data transfer: inputs are JSON
plus image arrays via shared memory (read-only views in the child); outputs are JSON and
artifact bytes returned to the parent, which writes them through `RuntimeStorage` (the
child gets no filesystem access to `ARE/`). Child startup cost is about 100–300 ms; a
warm pool can come later if profiling (PP-057) justifies it.

## Functional Requirements

- Implements the `Executor` protocol. The `SequentialExecutor` tests are parameterized to
  run against both executors.
- Timeouts terminate the child (TIMEOUT state, then SIGKILL after a grace period).
- Crash → FAILED with category `crashed` (new `FailureCategory`, a MINOR contract
  addition).
- Memory and CPU limits from settings (new `resources.plugin_memory_mb`,
  `resources.plugin_cpu_seconds`).
- `strict_priority_barriers` semantics preserved. Parallel execution within a stage is
  optional and behind `scheduling.max_parallel` (default 1).

## Technical Implementation

`src/photoproof/core/pipeline/executor_process.py`; the shared isolation helper in
`core/` (from PP-009). The settings schema adds the executor option and limits.

## Integration Requirements

The controller selects the executor from settings. Manifests record the executor kind.
Cache keys do not change (results must be identical).

## Security and Edge Cases

Pickle is only used between trusted parent and child processes (code is trusted;
untrusted data stays as bytes or arrays). Cleanup of shared-memory segments on crash.
Windows Job Object limits.

## Testing Requirements

The full executor test suite parameterized over both executors. Fixtures: segfault,
infinite loop, memory bomb. Determinism: identical manifests (computational fingerprint)
for both executors on the demo plugins.

## Acceptance Criteria

- [ ] All existing executor semantics tests pass with `ProcessExecutor`.
- [ ] Segfaulting and hanging plugins are contained (FAILED/TIMEOUT), and the run continues for unrelated plugins.
- [ ] Computational fingerprints are identical between executors on the demo plugins.
- [ ] No leaked processes or shared-memory segments after the failure tests.

## Expected Outcome

Native-code failures are contained and timeouts are enforceable.

## Documentation Requirements

Execution lifecycle (executor section, limitation removed), settings reference, security
model.

## Dependencies

PP-009 (shared isolation helper).

## Priority and Effort

P1, large.

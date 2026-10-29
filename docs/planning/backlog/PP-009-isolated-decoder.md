---
id: PP-009
title: "security(imaging): run image decoders in an isolated, resource-limited worker process"
milestone: M1
labels: [type:security, priority:P1, area:security, area:imaging, effort:large, status:blocked]
depends_on: [PP-073]
integrates_with: [PP-003]
---
## Summary

Move all decoding of untrusted bytes into a short-lived child process with CPU-time,
memory and wall-clock limits. The worker returns only validated pixel buffers and
metadata.

## Problem Statement

Image codecs are native code that parses attacker-controlled input. A crash or
memory-corruption bug in Pillow, libheif, libjxl or libtiff currently runs inside the
main PhotoProof process, so it can crash the application or worse. In-process timeouts
cannot stop native code.

## Objective

A decoder crash, hang or memory blow-up affects only a disposable worker. The parent
reports `InputRejectedError` (or `DecoderCrashed`) and continues.

## Technical Background

- `multiprocessing` with the `spawn` start method (consistent across OSes; avoids
  forking a process with threads).
- Limits: POSIX `resource.setrlimit(RLIMIT_AS, RLIMIT_CPU)` in the child. On Windows,
  Job Objects via `ctypes` (`JOBOBJECT_EXTENDED_LIMIT_INFORMATION`), or document a
  wall-clock-only limit.
- Transfer: send the input bytes in; receive pixels via `multiprocessing.shared_memory`
  or pickled bytes, with a size check against `DecodeLimits`.

## Functional Requirements

- `IsolatedDecoder` implements `ImageDecoder` and delegates to the registered in-process
  adapter inside the child.
- Enforced limits: `timeout_seconds`, a memory ceiling derived from `max_pixels`
  (bytes = pixels × channels × bytes-per-channel × safety factor) and a CPU ceiling.
- Child exit codes and signals are mapped to typed errors: `timeout`, `memory_limit`,
  `crashed(signal)`, `rejected(reason)`.
- No network access is required by the worker. Document whether it is enforced per
  platform.
- The worker never writes to disk.

## Technical Implementation

- Module `src/photoproof/imaging/isolation.py`. A small pure-Python child entry point
  that imports only the adapter.
- Reuse the pattern for the process-isolated executor (PP-052). Keep a shared helper
  in `core/` if duplication appears; do not build a general sandbox framework.
- A setting `imaging.isolation: process | in_process` (default `process`; `in_process`
  only allowed when `environment.mode` is `development`). This requires a settings
  schema addition.

## Integration Requirements

The ingestion and decode step (PP-011, PP-012) calls `IsolatedDecoder`. Manifests record
the isolation mode used. The health check reports when isolation is unavailable.

## Security and Edge Cases

- A child that ignores SIGTERM → SIGKILL after a grace period.
- Zombie/orphan processes: always `join` and clean up; test it.
- Shared-memory leaks on crash: unlink in `finally` and in the parent.
- The parent validates the returned buffer length against the declared dimensions
  (never trust the child's metadata blindly).

## Testing Requirements

- A test adapter that segfaults (`ctypes.string_at(0)`), loops forever, or allocates
  without bound, to verify each error mapping on Linux, macOS and Windows (skip
  per-platform where a limit cannot be enforced, with a documented reason).
- Throughput benchmark: overhead per decode for a 12 MP JPEG.

## Acceptance Criteria

- [ ] A segfaulting decoder produces `DecoderCrashed`, and the application keeps running (test on all CI OSes).
- [ ] An infinite loop is terminated within `timeout_seconds + 2s`.
- [ ] Unbounded allocation is stopped by the memory limit on Linux and macOS; Windows behaviour is documented and tested where enforceable.
- [ ] No orphan processes or leaked shared memory segments remain after each failure test.
- [ ] Decode overhead is measured and documented.

## Expected Outcome

Untrusted image parsing can no longer take down PhotoProof.

## Documentation Requirements

Security model (runtime isolation plan step 1 marked implemented), settings reference.

## Dependencies

**Blocked by** (merge these before starting): PP-073.

**Integrates with** (start now; finish the integration once these land): PP-003.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P1, large. Security-critical before enabling the more complex codecs.

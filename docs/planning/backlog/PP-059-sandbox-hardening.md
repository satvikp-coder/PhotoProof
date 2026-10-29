---
id: PP-059
title: "security(imaging): harden decoder sandbox and native-library isolation"
milestone: M6
labels: [type:security, priority:P1, area:security, effort:large, status:needs-design]
depends_on: [PP-009, PP-058]
---
## Summary

Go beyond resource limits. Reduce what a compromised decoder worker can do: restrict
system calls and filesystem and network access per platform, and drop inherited handles
and environment.

## Problem Statement

PP-009 contains crashes and resource exhaustion, but a memory-corruption exploit in a
codec would still run with the user's full privileges inside the worker.

## Objective

A documented per-platform hardening profile for decoder (and, later, plugin) workers,
with tests that prove the restrictions are active.

## Technical Background

- **Linux:** seccomp-bpf (e.g. via `pyseccomp`/`libseccomp` bindings) allowing only the
  needed syscalls; `prctl(PR_SET_NO_NEW_PRIVS)`; Landlock for filesystem restriction
  (kernel ≥ 5.13); network namespaces where unprivileged user namespaces are available.
- **macOS:** `sandbox-exec` profiles are deprecated but functional. Document the risk
  and evaluate alternatives.
- **Windows:** AppContainer or a restricted token plus a Job Object (no child processes,
  UI restrictions).
- On every platform: close inherited file descriptors, a minimal environment, and the
  working directory set to an empty temporary directory.

## Functional Requirements

- A hardening level setting `imaging.worker_hardening: none | basic | strict` with
  per-platform capability detection, reported in `doctor` and manifests.
- `basic` (all platforms): fd closing, a clean environment, an empty working directory,
  no child processes.
- `strict` (where supported): syscall and filesystem restrictions. Attempts are logged.

## Technical Implementation

Extend `imaging/isolation.py`. A design note in the PR with a per-platform matrix of
mechanism, enforcement and test method. Status: needs-design.

## Integration Requirements

Also used by `ProcessExecutor` (PP-052) for plugins. Unsupported strict mode → degrade to
basic with a WARNING health finding (never silently).

## Security and Edge Cases

Seccomp allowlists break with library updates, so CI must exercise every decoder under
`strict`. Debuggability (an option to disable hardening in development only).

## Testing Requirements

In-worker probes: attempt a network connection, a file open outside the allowed
directory, and `fork`/`exec`. Each must fail under `strict` on supported platforms.
Decoder corpus (PP-058) passes under `strict`.

## Acceptance Criteria

- [ ] `basic` hardening is active on all CI platforms (probe tests).
- [ ] `strict` hardening blocks network, out-of-scope file access and exec on Linux (probe tests); other platforms are documented as supported or unsupported.
- [ ] The decoder corpus passes under the highest supported level.
- [ ] `doctor` and manifests report the effective hardening level.

## Expected Outcome

Defence in depth against codec exploits.

## Documentation Requirements

Security model (runtime isolation plan steps 1–3 updated).

## Dependencies

PP-009, PP-058.

## Priority and Effort

P1, large. Needs design.

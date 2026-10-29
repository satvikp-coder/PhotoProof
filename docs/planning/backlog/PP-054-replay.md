---
id: PP-054
title: "feat(core): replay historical runs with environment verification and restoration"
milestone: M6
labels: [type:feature, priority:P3, area:core, effort:large, status:needs-design]
depends_on: [PP-048, PP-001]
---
## Summary

Add `photoproof replay RUN_ID`. It re-executes a past run from its manifest using the
stored evidence, verifies (and optionally restores) the recorded environment, and diffs
the new manifest against the original.

## Problem Statement

Manifests record everything needed for reproducibility, but nothing re-executes them.
Verifying that a published result still reproduces requires manual reconstruction.

## Objective

A controlled replay that either reproduces the computational fingerprint or explains
exactly which recorded component differs.

## Technical Background

Replay levels:

1. **Same environment:** settings and profile from the manifest (not from the current
   files), same plugin versions required.
2. **Environment verification:** compare the recorded `software`, plugin
   `implementation_sha256` and distribution versions with the current ones.
3. **Restoration:** check out the recorded `git_commit` in a temporary worktree and
   create a venv from that commit's lockfile (controlled, explicit, slow).

## Functional Requirements

- `replay RUN_ID [--verify-only] [--restore-environment]`.
- Uses the original evidence from the evidence store (PP-001); missing evidence → a clear
  failure.
- Output: a new run whose manifest records `replay_of: RUN_ID`, plus a `manifest diff`
  (PP-048).
- Never overwrites the original run or manifest.

## Technical Implementation

`src/photoproof/core/replay.py`. Settings are reconstructed from the
`effective_settings` embedded in the manifest (requires
`reporting.include_effective_settings: true` at the time of the original run; otherwise
replay is limited, and says so).

## Integration Requirements

CLI. Later a WebKit "Re-run" button that calls the verify-only mode.

## Security and Edge Cases

Restoration runs `git worktree` and pip. It is a CONTROLLED action, so require explicit
confirmation and no network beyond the lockfile hashes. Manifests are trusted only if
their hash matches the database record.

## Testing Requirements

Replay of a demo-plugin run reproduces the fingerprint. A modified plugin implementation
→ the digest difference is reported. Missing evidence → a clear error.

## Acceptance Criteria

- [ ] Replaying an unchanged environment reproduces the computational fingerprint (test with demo plugins).
- [ ] Any differing component is reported by category.
- [ ] Original runs and manifests are never modified.
- [ ] Environment restoration is explicit, documented and covered by at least one CI smoke test.

## Expected Outcome

Verifiable reproducibility of past results.

## Documentation Requirements

Operations runtime docs (replay), ADR on replay levels.

## Dependencies

PP-048 (manifest diff), PP-001 (stored evidence).

## Priority and Effort

P3, large. Needs design.

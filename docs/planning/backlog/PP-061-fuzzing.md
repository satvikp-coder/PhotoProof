---
id: PP-061
title: "test(security): fuzz decoders and parsers and inject pipeline failures"
milestone: M6
labels: [type:testing, priority:P2, area:security, area:testing, effort:medium, status:blocked]
depends_on: [PP-058]
integrates_with: [PP-002]
---
## Summary

Add coverage-guided fuzzing for PhotoProof's own parsers (format detection, metadata,
DCT bitstream) and the decoder boundary, plus systematic failure injection across the
pipeline (storage, database, events, plugins).

## Problem Statement

Hand-written malformed cases cover known attack classes only. Unknown parser edge cases
and untested failure combinations remain.

## Objective

Atheris fuzz targets runnable locally and in a scheduled CI job, and a failure-injection
test matrix proving that every injected fault yields a documented, non-silent outcome.

## Technical Background

Atheris (libFuzzer for Python) instruments Python code and native extensions. Fuzz
targets: `detect_format(bytes)`, the EXIF/XMP parsers, the JPEG DCT parser (PP-016), and
`IsolatedDecoder.decode` (crash → expected error). Failure injection: monkeypatch
`atomic_write`, `Database._write`, subscriber handlers and the plugin entry points to
raise at each step, and assert the run status, persisted state and manifest.

## Functional Requirements

- `fuzz/` directory with targets and seed corpora (from PP-058 generators).
- A weekly scheduled workflow running each target for N minutes (Linux only), uploading
  crashes as artifacts. Crashes become regression cases in PP-058.
- A failure-injection matrix test (`pytest -m failure_injection`): {storage write, DB
  write, critical subscriber, optional subscriber, plugin raise, plugin timeout} × {before
  run, during plugin, after plugin, manifest write} → expected status and side effects.

## Technical Implementation

`fuzz/*.py`, `.github/workflows/fuzz.yml` (scheduled + manual; least privilege),
`tests/resilience/test_failure_injection.py`.

## Integration Requirements

Security workflow summary links the fuzz results.

## Security and Edge Cases

Fuzz artifacts may contain crashing inputs, so upload them privately as workflow
artifacts (not issues). Avoid running fuzzers on PRs from forks.

## Testing Requirements

The deliverable is tests. Each injection cell has an assertion.

## Acceptance Criteria

- [ ] Fuzz targets exist for format detection, metadata parsing and the decoder boundary, and run locally.
- [ ] The scheduled fuzz workflow runs and uploads findings.
- [ ] The failure-injection matrix is fully implemented, and no cell results in a silent success.

## Expected Outcome

Continuous discovery of parser bugs and verified failure handling.

## Documentation Requirements

The testing page, security model.

## Dependencies

**Blocked by** (merge these before starting): PP-058.

**Integrates with** (start now; finish the integration once these land): PP-002.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P2, medium.

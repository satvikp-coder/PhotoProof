---
id: PP-049
title: "feat(cli): inspect failed plugins with developer diagnostics"
milestone: M5
labels: [type:feature, priority:P2, area:core, effort:small, status:ready]
depends_on: []
---
## Summary

Add `photoproof diagnose RUN_ID [--plugin ID]`, which shows the failure category,
exception type, traceback, dependency status, configuration, inputs summary and timing
for failed or skipped plugins, and a WebKit Diagnostics page once PP-038 exists.

## Problem Statement

Tracebacks are recorded in manifests, but finding them requires reading raw JSON.
Plugin developers need a fast feedback loop.

## Objective

A focused diagnostic view of non-successful plugins with actionable hints per failure
category.

## Technical Background

Failure categories are already typed (`FailureCategory`). Map each to a hint:
`invalid_output` → the declared vs produced outputs; `config_error` → the schema path;
`timeout` → the effective timeout and its source; `dependency_unavailable` → the chain.

## Functional Requirements

- Lists all non-SUCCESS plugins with state and category, or one plugin in detail.
- Shows the traceback only in the CLI (local). WebKit shows it only when
  `development.debug`.
- Includes a ready-to-run re-execution hint (`analyze … --no-cache --profile …`).

## Technical Implementation

`src/photoproof/cli.py` command, reading the manifest via `RuntimeStorage`.

## Integration Requirements

WebKit page later (PP-038 shell). Same data, debug-gated traceback.

## Security and Edge Cases

Tracebacks may contain file paths. CLI only; never shown in non-debug web UI. Missing
manifests → a clear error.

## Testing Requirements

CLI tests using manifests produced by the resilience fixtures (raise, invalid_output,
timeout, skipped).

## Acceptance Criteria

- [ ] `diagnose` shows every failure category with a specific hint (tests for each).
- [ ] Tracebacks never appear in WebKit unless debug is enabled.

## Expected Outcome

Faster plugin development and troubleshooting.

## Documentation Requirements

Plugin development guide (debugging section), operations troubleshooting.

## Dependencies

None.

## Priority and Effort

P2, small.

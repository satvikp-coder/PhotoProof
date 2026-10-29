---
id: PP-041
title: "feat(WebKit): display analysis progress and plugin status"
milestone: M5
labels: [type:feature, priority:P2, area:WebKit, effort:medium, status:blocked]
depends_on: [PP-039, PP-050]
---
## Summary

A live progress page for a submitted job, showing each planned plugin's state (PENDING →
RUNNING → SUCCESS/FAILED/…) as events arrive.

## Problem Statement

Analyses with many detectors can take tens of seconds. Users need feedback, and they need
to see which plugins failed or were skipped.

## Objective

A job status page fed by persisted plugin states (via the existing event persistence),
updated with polling or Server-Sent Events.

## Technical Background

The persistence subscriber already writes each plugin's state to `plugin_executions`.
The UI can poll `GET /jobs/{id}` (simple and robust) or use SSE. Prefer polling every 1 s
with HTMX initially.

## Functional Requirements

- A plan table: plugin name, priority stage, state badge (text plus colour, never colour
  alone), duration, failure category.
- Overall run status, and a link to results on completion.
- Skipped and failed plugins show their reason (from `dependency_status` and `message`).

## Technical Implementation

`WebKit/pages/job.*`; a read-only JSON endpoint built on the `Database` reads. Requires
background execution (PP-050).

## Integration Requirements

Reads `runs` and `plugin_executions`. No new events required.

## Security and Edge Cases

Job IDs are unguessable (UUID4 hex already). No tracebacks shown unless
`development.debug`. Long-running jobs or a browser reconnect.

## Testing Requirements

Endpoint tests for each state transition, and the rendering of failure reasons.

## Acceptance Criteria

- [ ] Plugin states update while a job runs (test with a slow fixture plugin).
- [ ] State badges carry text labels (accessibility).
- [ ] Failure and skip reasons are visible without tracebacks in non-debug mode.

## Expected Outcome

Transparent, live execution feedback.

## Documentation Requirements

The user guide, and the architecture page (web ↔ jobs data flow).

## Dependencies

PP-039, PP-050.

## Priority and Effort

P2, medium.

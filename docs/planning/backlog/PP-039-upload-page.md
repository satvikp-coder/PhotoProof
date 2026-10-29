---
id: PP-039
title: "feat(WebKit): build the single-image analysis upload page"
milestone: M5
labels: [type:feature, priority:P1, area:WebKit, effort:medium, status:blocked]
depends_on: [PP-038]
integrates_with: [PP-011]
---
## Summary

An Analyze page where a user uploads one image, chooses a single-image profile and starts
an analysis. Results open on a result page (PP-042, PP-043).

## Problem Statement

Analyses are currently CLI-only.

## Objective

A streaming, limit-enforcing upload that hands bytes to `IngestionService` and runs the
controller, with clear errors for rejected files.

## Technical Background

Uploads must be streamed to the ingestion service (never buffered fully in memory) and
size-capped server-side, regardless of the client's `Content-Length`. The client-side
`accept=` attribute is only a convenience, not a control.

## Functional Requirements

- A drag-and-drop or file-picker control with an accessible label, plus a profile
  selector (single-mode profiles only, from configuration).
- Server-side: stream into `IngestionService.ingest`, then `AnalysisController.analyze`.
  Run synchronously until PP-050 exists, with a spinner and a request timeout setting.
- Display rejections (size, format) with the documented messages.
- Show the privacy notice: where files are stored and how to delete them (PP-056).

## Technical Implementation

`WebKit/pages/analyze.*`. Reuse controller APIs. No forensic logic in WebKit.

## Integration Requirements

Uses PP-011 (ingestion). When PP-050 lands, switch to job submission and redirect to the
progress page (PP-041).

## Security and Edge Cases

CSRF token required. Per-request upload cap. Concurrent uploads (serialize or reject
while synchronous). File names are displayed escaped. Request timeouts.

## Testing Requirements

HTTP tests: a valid upload runs to a result page; oversize → 413 with a message; an
unknown format → rejected; a missing CSRF token → 403.

## Acceptance Criteria

- [ ] Uploads are streamed and capped server-side (oversized request rejected before full read; test).
- [ ] The analysis runs through the controller and links to the result page.
- [ ] Rejections show actionable messages.
- [ ] The page passes the accessibility checklist.

## Expected Outcome

Users can analyse an image from the browser.

## Documentation Requirements

A user guide page for analysis in the web UI.

## Dependencies

**Blocked by** (merge these before starting): PP-038.

**Integrates with** (start now; finish the integration once these land): PP-011.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P1, medium.

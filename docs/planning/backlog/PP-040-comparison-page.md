---
id: PP-040
title: "feat(WebKit): build the two-image comparison page"
milestone: M5
labels: [type:feature, priority:P1, area:WebKit, effort:medium, status:blocked]
depends_on: [PP-038]
integrates_with: [PP-039, PP-027, PP-030]
---
## Summary

A Compare page for uploading a target and a reference, running the comparison profile,
and viewing aligned results: a swipe/fade viewer, the SSIM map and change regions.

## Problem Statement

Comparison results are hard to read without side-by-side and overlay views.

## Objective

Two labelled upload slots ("Target" and "Reference (user-supplied, not assumed
authentic)"), comparison results with registration quality, and an interactive viewer.

## Technical Background

Common comparison UI patterns: side-by-side with synchronized zoom and pan, a swipe
slider, and opacity fade, using the aligned reference from PP-028 so that the pixels
correspond.

## Functional Requirements

- Two upload inputs; validation messages per slot.
- The result view shows registration status and quality first. If registration failed,
  say so and show no difference maps.
- Viewer: side-by-side, swipe and fade modes; a toggleable change-region overlay; a
  region list linked to the viewer (click to zoom).
- All images displayed are previews (PP-010).

## Technical Implementation

`WebKit/pages/compare.*`, plus a small vanilla-JS viewer component in
`WebKit/components/` (no heavy front-end framework), keyboard-operable.

## Integration Requirements

The controller with the `comparison` profile. Outputs of PP-028, PP-029 and PP-030.

## Security and Edge Cases

Both uploads go through ingestion. A mismatch where the reference is huge. Escaped file
names. The same file uploaded twice → the "identical inputs" message (PP-027).

## Testing Requirements

HTTP tests for the flow. A viewer component test (Playwright, optional). Registration
failure rendering.

## Acceptance Criteria

- [ ] Comparison runs from the browser and displays registration quality before any difference map.
- [ ] The swipe, fade and side-by-side modes work by keyboard and mouse.
- [ ] The reference label states that it is not assumed authentic.
- [ ] Registration failure shows an explanatory message and no difference maps.

## Expected Outcome

An effective visual comparison workflow.

## Documentation Requirements

A user guide page for comparison.

## Dependencies

**Blocked by** (merge these before starting): PP-038.

**Integrates with** (start now; finish the integration once these land): PP-039, PP-027, PP-030.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P1, medium.

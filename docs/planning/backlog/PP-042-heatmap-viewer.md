---
id: PP-042
title: "feat(WebKit): build a forensic heatmap viewer with image overlays"
milestone: M5
labels: [type:feature, priority:P1, area:WebKit, effort:medium, status:blocked]
depends_on: [PP-039, PP-036]
---
## Summary

A result-page viewer for the preview with toggleable layers: the fused heatmap, region
outlines and individual detector maps. It provides opacity control, zoom/pan and a
legend.

## Problem Statement

A static image cannot show several layers or let investigators look at details.

## Objective

An accessible viewer that renders artifacts from PP-036 and the detector maps, with the
legend text from `fusion.legend`.

## Technical Background

Layered `<canvas>` or CSS-stacked `<img>` elements with `mix-blend-mode`/opacity; zoom
and pan through CSS transforms. All layers share the preview's resolution.

## Functional Requirements

- Layer list with checkboxes: fused heatmap, regions, and each available detector map
  (unavailable detectors listed and greyed, with the reason).
- An opacity slider, zoom (buttons plus wheel), pan, and a reset.
- The legend displays the scale label "heuristic suspicion score (uncalibrated)" until
  PP-037.
- No fused map → a prominent "inconclusive / no fused evidence" message instead of an
  empty overlay.

## Technical Implementation

`WebKit/components/viewer.*` (vanilla JS module, no build step). Artifacts are served
through a read-only endpoint that resolves artifact SHA-256 → file via `RuntimeStorage`
(safe paths; only artifacts referenced by the run).

## Integration Requirements

Reads the run manifest to enumerate layers and artifacts.

## Security and Edge Cases

The artifact endpoint must refuse hashes not referenced by the requested run's manifest
(no arbitrary blob reads). Content-Type is taken from the artifact metadata. Large
previews.

## Testing Requirements

Endpoint authorization test (an unreferenced hash → 404). Viewer interaction tests
(Playwright, optional). The no-map rendering.

## Acceptance Criteria

- [ ] All available layers can be toggled and blended; unavailable ones show their reason.
- [ ] The artifact endpoint serves only artifacts referenced by the run (test).
- [ ] The legend label matches `fusion.legend`.
- [ ] The viewer is operable by keyboard.

## Expected Outcome

Investigators can explore the evidence visually.

## Documentation Requirements

A user guide page on reading the heatmap, with caveats.

## Dependencies

PP-039, PP-036.

## Priority and Effort

P1, medium.

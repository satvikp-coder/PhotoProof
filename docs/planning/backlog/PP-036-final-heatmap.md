---
id: PP-036
title: "feat(plugins): generate the final forensic heatmap artifact"
milestone: M4
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-032]
integrates_with: [PP-035, PP-010]
---
## Summary

Plugin `fusion.heatmap` renders the fused heuristic map and region outlines as a colour
heatmap overlay artifact on the safe preview. Its legend states the scale's meaning and
its limitations.

## Problem Statement

Investigators need one visual summary. Colour choices and legends strongly affect how
results are read: a red blob reads as "fake" unless explained.

## Objective

Deterministic PNG artifacts: the heatmap alone, the overlay on the preview, and the
region outlines, plus a legend specification for the UI and reports.

## Technical Background

Use a perceptually uniform, colour-vision-deficiency-safe colormap (e.g. `cividis`, or
`viridis`). Avoid rainbow/jet colormaps, which create false boundaries. Alpha-blend over
the grayscale preview to keep context. The legend reads "heuristic suspicion score
(uncalibrated)".

## Functional Requirements

- Outputs: `fusion.heatmap` (PNG), `fusion.overlay` (PNG at preview resolution),
  `fusion.legend` (JSON: colormap, range, label, notes).
- Regions below the display threshold are transparent.
- A run with no fused map produces no heatmap. The UI then shows "no fused evidence
  available" (inconclusive), never an empty "clean" heatmap.

## Technical Implementation

`src/photoproof/plugins/fusion/heatmap/`. Embed the colormap lookup table as a constant
(256×3), so no matplotlib dependency is needed. OpenCV for drawing outlines.

## Integration Requirements

Plugin ID `fusion.heatmap`; priority 100; requires `fusion.regions` and
`support.preview`; outputs above. Displayed by PP-042 and embedded in reports.

## Security and Edge Cases

Only previews are used (no originals). Determinism (fixed blending). Grayscale previews.

## Testing Requirements

Determinism (byte-identical outputs). Colormap LUT correctness against a reference
table. The no-map case.

## Acceptance Criteria

- [ ] Heatmap outputs are byte-identical across runs.
- [ ] The legend specification includes the "uncalibrated heuristic" label.
- [ ] No heatmap is produced when no fused map exists, and the documented message is shown instead.
- [ ] The colormap is documented as colour-vision-deficiency safe.

## Expected Outcome

A clear, honest visual summary of the fused evidence.

## Documentation Requirements

A fusion page section on visualization, and the WebKit viewer docs.

## Dependencies

**Blocked by** (merge these before starting): PP-032.

**Integrates with** (start now; finish the integration once these land): PP-035, PP-010.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P1, medium.

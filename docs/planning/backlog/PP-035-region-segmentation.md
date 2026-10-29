---
id: PP-035
title: "feat(plugins): segment suspicious regions with morphological cleanup"
milestone: M4
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-033]
---
## Summary

Plugin `fusion.regions` converts the fused heuristic map into cleaned, labelled
suspicious regions (masks plus polygons) for localization metrics and display.

## Problem Statement

Continuous maps are hard to evaluate (IoU/F1 need binary masks) and hard to explain.
Naive thresholding produces speckle and fragmented regions.

## Objective

A binary localization mask and a region list with per-region evidence references from
`fusion.evidence`.

## Technical Background

Hysteresis thresholding (high and low thresholds: seeds above high, grown while above
low) reduces speckle better than a single threshold. Then: morphological opening
(remove small noise), closing (fill small gaps), hole filling, component area filtering,
and polygon simplification (Douglas–Peucker) for display.

## Functional Requirements

- Config: `high`, `low`, kernel sizes, `min_area_fraction`, polygon tolerance.
- Outputs: `fusion.mask` (PNG artifact), `fusion.regions` (JSON: `id`, polygon, bbox,
  area, max/mean score, contributing detectors).
- Thresholds are documented as provisional until PP-037.

## Technical Implementation

`src/photoproof/plugins/fusion/regions/`; OpenCV morphology and `findContours`;
`approxPolyDP`.

## Integration Requirements

Plugin ID `fusion.regions`; priority 100; requires `fusion.rules`; outputs above.
Consumed by the heatmap (PP-036), the evaluation harness (PP-066) and reports.

## Security and Edge Cases

An empty fused map → empty mask with a status. A full-image region (likely global
processing, so flag it). Many small regions (cap the count, record the truncation).

## Testing Requirements

Synthetic maps with planted blobs plus speckle → planted blobs recovered (IoU ≥ 0.9) and
speckle removed. Hysteresis behaviour tests.

## Acceptance Criteria

- [ ] Planted blobs are recovered with IoU ≥ 0.9 and speckle is removed in synthetic tests.
- [ ] Every region references its contributing detectors.
- [ ] The mask format matches the evaluation harness (PP-066).

## Expected Outcome

Clean localization output for metrics, UI and reports.

## Documentation Requirements

A fusion page section on segmentation.

## Dependencies

PP-033.

## Priority and Effort

P1, medium.

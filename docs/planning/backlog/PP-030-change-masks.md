---
id: PP-030
title: "feat(plugins): extract comparison change regions and binary change masks"
milestone: M3
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-072]
integrates_with: [PP-029]
---
## Summary

Plugin `comparison.change_mask` thresholds the dissimilarity map into a binary change
mask and a list of change regions (bounding boxes, areas, mean dissimilarity), with
morphological cleanup.

## Problem Statement

Investigators need discrete regions ("this area changed"), and reports need region
lists, not only continuous maps.

## Objective

A binary mask plus a region table, each region labelled a *confirmed difference between
the inputs* (distinct from a suspected manipulation).

## Technical Background

Choose the threshold automatically with Otsu's method on the dissimilarity histogram
within the overlap, bounded by a configured minimum (to avoid flagging pure noise when no
change exists). Clean up with opening and closing, discard components smaller than
`min_region_area`, and label the rest with `cv2.connectedComponentsWithStats`.

## Functional Requirements

- Config: `threshold` (`otsu`|float), `min_threshold` (default 0.15), `min_region_area`
  (default 0.05% of the image), kernel size.
- Outputs: `comparison.change_mask` (PNG artifact) and `comparison.change_regions` (JSON:
  `id`, `bbox`, `area`, `mean_dissimilarity`, `category: "difference"`).
- No regions → `status: no_differences_detected` (at the configured sensitivity).

## Technical Implementation

`src/photoproof/plugins/comparison/change_mask/`; OpenCV.

## Integration Requirements

Plugin ID `comparison.change_mask`; priority 30; requires `comparison.ssim`. Becomes a
`required_outputs` entry of the comparison profile. It feeds reporting (PP-031) and is
displayed in WebKit (PP-040).

## Security and Edge Cases

All-different images (one giant region; report low specificity). A noisy map (many tiny
regions removed). Thresholds that depend on JPEG quality (document them).

## Testing Requirements

Planted edits of known area → mask IoU recorded (target ≥ 0.7 on clean synthetic edits).
Identical-after-recompression pairs → no regions above the minimum threshold.

## Acceptance Criteria

- [ ] Clean synthetic edits reach mask IoU ≥ 0.7.
- [ ] Recompressed identical pairs produce `no_differences_detected` at default sensitivity.
- [ ] Regions are labelled as differences, not as manipulations, in every output.

## Expected Outcome

Discrete, reportable change regions between two inputs.

## Documentation Requirements

The comparison chain page.

## Dependencies

**Blocked by** (merge these before starting): PP-072.

**Integrates with** (start now; finish the integration once these land): PP-029.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P1, medium.

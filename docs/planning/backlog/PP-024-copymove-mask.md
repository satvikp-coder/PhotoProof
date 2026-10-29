---
id: PP-024
title: "feat(plugins): generate copy-move region masks from verified transforms"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-023]
---
## Summary

Plugin `copymove.mask` turns verified transforms into pixel-level source and target
masks, by dense correlation between each region and its transformed counterpart.

## Problem Statement

Keypoint inliers are sparse. Localization metrics (IoU/F1/MCC) and the heatmap need
dense masks.

## Objective

Binary masks (source, target, union) plus a per-pixel correlation map, as artifacts.

## Technical Background

Given affine `A`, warp the image by `A` and compute the local zero-mean normalized
cross-correlation (ZNCC) between `I` and `warp(I, A)` in windows (e.g. 7×7) around the
inlier convex hull. Copied pixels have ZNCC ≈ 1. Threshold, then apply morphological
opening and closing, and keep connected components that overlap the inlier hull (Amerini
et al. 2013; Christlein et al. 2012 benchmark).

## Functional Requirements

- Config: `zncc_window` (default 7), `zncc_threshold` (default 0.9; to be calibrated in
  PP-037), `dilate_hull_px` (default 32), morphology kernel size.
- Outputs: `copymove.mask` (artifact 8-bit PNG: 0 background, 1 source, 2 target),
  `copymove.correlation_map` (AnomalyMap-compatible float map), and `copymove.regions`
  (JSON polygons and areas).
- No verified transforms → empty mask with `status: no_copy_move_found`, documented as
  "no copy-move detected by this method", **not** "authentic".

## Technical Implementation

`src/photoproof/plugins/copymove/mask/`. OpenCV `warpAffine`, box filters for local
ZNCC (integral images), and `morphologyEx`.

## Integration Requirements

Plugin ID `copymove.mask`; priority 40; requires `copymove.verification`; inputs
`copymove.verified`, `image.working`; outputs above. Fusion (PP-033) treats verified
copy-move as strong evidence.

## Security and Edge Cases

Warped regions outside the image bounds (clip). Overlapping source and target (small
displacement): handle by excluding pixels whose displacement is below the minimum.
Flat regions (ZNCC undefined: variance near zero → excluded).

## Testing Requirements

Planted copy-move fixtures: mask IoU against ground truth recorded as a baseline (target
IoU ≥ 0.7 on clean synthetic translations; report, do not tune to the fixture).
Rotation and scale fixtures. No-copy input → empty mask with the documented status.

## Acceptance Criteria

- [ ] Clean synthetic translation copies reach IoU ≥ 0.7 against planted masks.
- [ ] Rotation and scale fixtures produce masks (IoU recorded as a baseline).
- [ ] Empty results are labelled `no_copy_move_found`, never "authentic".
- [ ] Mask format is documented and consumed by the evaluation harness (PP-066).

## Expected Outcome

Pixel-level copy-move localization.

## Documentation Requirements

The copy-move chain page: masks and the semantics of the outputs.

## Dependencies

PP-023.

## Priority and Effort

P1, medium.

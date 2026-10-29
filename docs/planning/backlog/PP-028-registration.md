---
id: PP-028
title: "feat(plugins): register reference and target images with feature matching and geometric transforms"
milestone: M3
labels: [type:feature, priority:P1, area:plugins, area:imaging, effort:large, status:blocked]
depends_on: [PP-072]
integrates_with: [PP-027, PP-020, PP-012]
---
## Summary

Plugin `comparison.registration` estimates the geometric transformation that aligns the
reference to the target (similarity, affine or homography) and produces an aligned
reference plus a validity mask.

## Problem Statement

Pixel-wise comparison is meaningless unless the images are aligned. Crops, rescaling,
rotation and perspective changes are common between versions of a photograph.

## Objective

An aligned reference image in the target's coordinate frame, the transform, its quality
metrics, and a mask of target pixels covered by the reference.

## Technical Background

Detect keypoints in both images (reuse the SIFT parameters from PP-020; ORB is a faster
alternative). Match across images with a 2NN Lowe ratio (τ = 0.75). Estimate a
homography with RANSAC/USAC (`cv2.findHomography`), with an affine fallback when the
points are near-degenerate. Optionally refine with ECC (`cv2.findTransformECC`) for
sub-pixel accuracy. Validate: inlier count, inlier ratio, reprojection RMSE, and the
determinant and condition number of the transform.

## Functional Requirements

- Config: model (`homography`|`affine`|`similarity`), ratio, RANSAC threshold, `refine_ecc`.
- Outputs: `comparison.transform` (matrix, model, inliers, rmse, quality), the aligned
  reference artifact, and `comparison.overlap_mask`.
- Registration failure (too few inliers or a degenerate transform) → the plugin
  *succeeds* with `status: registration_failed`, and downstream comparison plugins are
  `NOT_APPLICABLE` (they must check). It never falls back silently to unaligned
  comparison.

## Technical Implementation

`src/photoproof/plugins/comparison/registration/`. OpenCV. Warp with
`cv2.warpPerspective` (bilinear; borders masked). Seeded RNG.

## Integration Requirements

Plugin ID `comparison.registration`; priority 10; inputs `image.working`,
`image.reference_working`; outputs above. Required by PP-029. In the comparison profile,
mark it `required_plugins` so that failure blocks or aborts per policy.

## Security and Edge Cases

Mirror images (negative determinant; detect and flag). Strong perspective (homography
validity checks). Totally unrelated images (failure path). Very different resolutions
(match at a common scale).

## Testing Requirements

Synthetic pairs: the target is the reference warped by known similarity, affine and
homography transforms plus noise → recovered parameters within tolerance (corner
reprojection error ≤ 1.5 px). An unrelated pair → `registration_failed`.

## Acceptance Criteria

- [ ] Known transforms recovered with corner error ≤ 1.5 px on synthetic pairs (seeded).
- [ ] Unrelated pairs produce `registration_failed`, and downstream plugins become `NOT_APPLICABLE`.
- [ ] Overlap mask excludes pixels without reference coverage.
- [ ] Transform quality metrics are recorded in the manifest.

## Expected Outcome

Accurate alignment, which makes difference maps meaningful.

## Documentation Requirements

A comparison chain page (registration → SSIM → change mask → reporting).

## Dependencies

**Blocked by** (merge these before starting): PP-072.

**Integrates with** (start now; finish the integration once these land): PP-027, PP-020, PP-012.

**Work plan (two PRs).** PR 1, mergeable now: implement the algorithm as a pure NumPy function in the plugin's `algorithm.py` (scaffold it with PP-074), unit-tested with the PP-072 synthetic generators; reference this issue with `Refs`. PR 2, after PP-012 is merged: add the `processor.py` wiring and an integration test through the controller; this PR uses `Closes`.

## Priority and Effort

P1, large.

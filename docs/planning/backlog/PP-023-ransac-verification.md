---
id: PP-023
title: "feat(plugins): verify copy-move clusters with RANSAC affine estimation"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-022]
---
## Summary

Plugin `copymove.verification` estimates a geometric transformation for each cluster
with RANSAC, and keeps only clusters with enough geometrically consistent inliers.

## Problem Statement

Clusters can still be coincidental. A real copy-move region is consistent with a single
affine (or similarity) transformation from source to target.

## Objective

Verified hypotheses, each with a transform matrix, inlier set, reprojection error and a
documented confidence score.

## Technical Background

RANSAC (Fischler & Bolles 1981): repeatedly sample minimal sets (3 pairs for affine,
2 for similarity), fit, and count inliers within a reprojection threshold ε; refit on the
best inlier set. Use OpenCV `estimateAffine2D` / `estimateAffinePartial2D` with
`cv2.RANSAC` or USAC_MAGSAC, a fixed seed, and an explicit confidence and iteration
limit. Degenerate transforms (near-zero determinant, extreme scaling or shear) indicate
spurious fits.

## Functional Requirements

- Config: `model` (`affine`|`similarity`, default `similarity`), `reproj_threshold`
  (default 3 px), `min_inliers` (default 6), `max_iterations`, `scale_range`
  (default 0.5–2.0), `max_shear`.
- Output `copymove.verified` (JSON: `cluster_id`, `matrix`, `inliers`, `inlier_ratio`,
  `rmse`, `scale`, `rotation_deg`, `score`). `score` is documented as a heuristic,
  **not a probability**.
- Clusters that fail are reported with the reason (`too_few_inliers`, `degenerate`).
- Repetitive-structure flag: transforms that are pure translations aligned with a
  periodic texture (detected via autocorrelation of the region) are flagged
  `possible_repetitive_structure`.

## Technical Implementation

`src/photoproof/plugins/copymove/verification/`. Set the OpenCV RNG seed from
`ctx.seed`.

## Integration Requirements

Plugin ID `copymove.verification`; priority 30; requires `copymove.clustering`; inputs
`copymove.clusters` and `copymove.matches`/`copymove.keypoints` (declare the requires
chain so the inputs are provided). Outputs `copymove.verified`.

## Security and Edge Cases

Clusters with fewer than the minimal sample → skip with a reason. Collinear points →
degenerate. Iteration caps prevent long runtimes.

## Testing Requirements

Planted similarity transforms (translation, rotation 0–90°, scale 0.7–1.3): the recovered
parameters are within tolerance. Random clusters are rejected. A tiled-floor fixture
(synthetic periodic texture) → `possible_repetitive_structure` flag.

## Acceptance Criteria

- [ ] Recovered rotation within ±2° and scale within ±3% on planted fixtures (seeded).
- [ ] Random clusters are rejected with the documented reasons.
- [ ] Periodic-texture fixture is flagged as possible repetitive structure.
- [ ] `score` is documented as heuristic, and no output field is called "probability".

## Expected Outcome

Copy-move hypotheses backed by geometric evidence.

## Documentation Requirements

The copy-move chain page: verification, the repetitive-structure policy, and how
evaluation treats naturally repeated content (windows, tiles, crowds). This feeds
PP-067.

## Dependencies

PP-022.

## Priority and Effort

P1, medium.

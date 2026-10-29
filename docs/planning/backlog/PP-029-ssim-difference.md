---
id: PP-029
title: "feat(plugins): compute SSIM-based difference maps on aligned images"
milestone: M3
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-028]
---
## Summary

Plugin `comparison.ssim` computes a local structural-similarity map between the target
and the aligned reference within the overlap mask, plus a complementary absolute
difference map.

## Problem Statement

Raw absolute differences are dominated by compression noise, exposure changes and slight
misregistration. SSIM is more robust to luminance and contrast changes and highlights
structural changes.

## Objective

A dissimilarity map `D = (1 − SSIM)/2 ∈ [0, 1]` at full resolution, restricted to the
overlap, with summary statistics.

## Technical Background

SSIM (Wang et al. 2004): `SSIM = ((2μxμy + C1)(2σxy + C2)) / ((μx²+μy²+C1)(σx²+σy²+C2))`
with Gaussian windows (σ = 1.5, 11×11), `C1 = (0.01L)²` and `C2 = (0.03L)²`. Multi-scale
SSIM improves robustness to resolution differences. Before SSIM, apply optional global
photometric normalization (match histograms or fit a gain/offset by least squares within
the overlap) to suppress exposure changes. Record it when applied.

## Functional Requirements

- Config: window σ, `multiscale` (bool), `photometric_normalization` (`none`|`gain_offset`|`histogram`).
- Outputs: `comparison.dissimilarity_map` (float map artifact), `comparison.abs_diff_map`,
  and `comparison.similarity_summary` (global SSIM, overlap fraction).
- `NOT_APPLICABLE` when registration failed.

## Technical Implementation

Implement SSIM with OpenCV Gaussian filtering in float32 (avoids adding scikit-image), or
justify scikit-image in the PR. Package `src/photoproof/plugins/comparison/ssim/`.

## Integration Requirements

Plugin ID `comparison.ssim`; priority 20; requires `comparison.registration`; inputs the
aligned reference, `image.working` and the overlap mask; outputs above.

## Security and Edge Cases

Overlap below 20% → low confidence. Large exposure differences (normalization). Residual
misregistration produces edge-shaped false differences (document it, and suppress with a
small morphological erosion of edge responses, recorded).

## Testing Requirements

Known-answer: the SSIM of identical images is 1. Compare against a reference
implementation on fixed arrays (tolerance 1e-4). A planted local edit (inpainted patch)
gives maximum dissimilarity in the patch. A global brightness change with normalization
→ low dissimilarity.

## Acceptance Criteria

- [ ] SSIM matches a reference implementation within 1e-4 on fixed test arrays.
- [ ] A planted local edit is the top region of the dissimilarity map.
- [ ] A global exposure change does not dominate the map when normalization is enabled.
- [ ] Outputs are restricted to the overlap mask.

## Expected Outcome

A meaningful, documented difference map between aligned images.

## Documentation Requirements

The comparison chain page: SSIM interpretation and limitations.

## Dependencies

PP-028.

## Priority and Effort

P1, medium.

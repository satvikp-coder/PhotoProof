---
id: PP-019
title: "feat(plugins): estimate local noise variance with MAD and build a noise-consistency map"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-072, PP-013]
integrates_with: [PP-018, PP-012]
---
## Summary

Plugin `noise.variance_map` estimates the local noise level per block from the residual
(PP-018) and produces a robust noise-consistency anomaly map, which highlights regions
whose noise level is inconsistent with the rest of the image.

## Problem Statement

A global noise estimate hides local inconsistency. Spliced content typically carries a
different noise level (another camera, ISO, resampling or denoising).

## Objective

A per-block σ map plus a robust-z consistency map, with masks for blocks where
estimation is unreliable (saturated, flat or textured).

## Technical Background

Local σ via MAD on residual blocks: `σ_b = 1.4826 · median(|R_b − median(R_b)|)`. Signal
dependence: real sensor noise varies with intensity (Poisson–Gaussian model), so
compare blocks within intensity bins, or regress σ against mean intensity and use the
residuals of that fit (Mahdian & Saic 2009; Lyu, Pan & Zhang 2014 use kurtosis-based
local estimation). Texture inflates the MAD, so exclude blocks with high
gradient energy.

## Functional Requirements

- Outputs `noise.sigma_map` (AnomalyMap scale `sigma`), `noise.consistency_map`
  (AnomalyMap robust-z after intensity-dependence correction), and a `valid_mask`
  excluding saturated (> 98% or < 2% intensity), flat and high-texture blocks.
- Config: block size (default 32), intensity bins (default 8), texture threshold.
- Summary: the fraction of valid blocks; < 30% → `confidence: low` (threshold documented
  as a tunable starting point, to be calibrated in PP-037).

## Technical Implementation

`src/photoproof/plugins/noise/variance_map/`; uses `imaging.stats` (PP-013). The
intensity correction uses a robust regression (Theil–Sen, or a binned median) to avoid
outlier influence.

## Integration Requirements

Plugin ID `noise.variance_map`; priority 20; requires `noise.residual`; inputs
`noise.residual`, `image.decoded`; outputs above. Optional; strength medium in fusion.

## Security and Edge Cases

Strong JPEG compression (noise largely removed, so low confidence). Images that are
mostly sky or flat walls (few valid blocks). Night images with clipped blacks.

## Testing Requirements

Synthetic: an image with σ=3 globally and σ=8 in one region → the region has the top z.
Signal-dependent noise without a splice → no block above |z| > 4 after correction
(seeded). Low-valid-fraction case.

## Acceptance Criteria

- [ ] The planted noise-level splice is detected (top-ranked region) in seeded tests.
- [ ] Signal-dependent noise alone does not produce outliers above the documented level after correction.
- [ ] `valid_mask` and `confidence` are produced and documented.
- [ ] Baseline FP statistics on authentic images are recorded for PP-037.

## Expected Outcome

Localized noise inconsistency evidence with explicit reliability information.

## Documentation Requirements

A detector page: method, references, limitations, false positives (in-camera local tone
mapping, HDR merges, portrait-mode blur).

## Dependencies

**Blocked by** (merge these before starting): PP-072, PP-013.

**Integrates with** (start now; finish the integration once these land): PP-018, PP-012.

**Work plan (two PRs).** PR 1, mergeable now: implement the algorithm as a pure NumPy function in the plugin's `algorithm.py` (scaffold it with PP-074), unit-tested with the PP-072 synthetic generators; reference this issue with `Refs`. PR 2, after PP-012 is merged: add the `processor.py` wiring and an integration test through the controller; this PR uses `Closes`.

## Priority and Effort

P1, medium.

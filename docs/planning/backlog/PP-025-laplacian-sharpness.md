---
id: PP-025
title: "feat(plugins): measure local Laplacian sharpness inconsistency"
milestone: M2
labels: [type:feature, priority:P2, area:plugins, effort:small, status:blocked]
depends_on: [PP-072, PP-013]
integrates_with: [PP-012]
---
## Summary

Plugin `visual.sharpness` maps local focus and sharpness with the variance of the
Laplacian, and flags regions whose sharpness is inconsistent with their depth context.
It is a weak visual cue.

## Problem Statement

Pasted objects often have different blur from their surroundings: they come from another
focal plane, are resampled, or are feathered. A sharpness map gives investigators a cue,
and fusion a weak signal.

## Objective

A per-block sharpness map (log variance of the Laplacian) and a robust-z inconsistency
map.

## Technical Background

Variance of the Laplacian (Pech-Pacheco et al. 2000) is a standard focus measure:
`S_b = Var(∇²I)` over block b. It scales with texture and contrast, so normalize by local
contrast (divide by the block's intensity variance plus ε) to separate blur from
low-contrast content.

**How it differs from PP-026:** this measures *blur level inside regions*, while PP-026
measures *the character of boundaries between regions*. They are reported separately and
fused as independent weak cues.

## Functional Requirements

- Config: block (default 32), `contrast_normalize` (default true).
- Outputs `visual.sharpness_map` (AnomalyMap scale `log_var_laplacian`) and
  `visual.sharpness_anomaly` (robust-z).
- An interpretation note: legitimate depth-of-field produces large, smooth sharpness
  variation, which is expected and not suspicious.

## Technical Implementation

`src/photoproof/plugins/visual/sharpness/`; OpenCV `Laplacian` (ksize 3, CV_32F) on
luminance; `imaging.stats`.

## Integration Requirements

Plugin ID `visual.sharpness`; priority 10; inputs `image.working`; outputs above;
optional, weak strength in fusion.

## Security and Edge Cases

Bokeh and portrait-mode images (large false positives; document them). Motion blur.
Noise inflating the Laplacian (optionally pre-filter, recorded).

## Testing Requirements

A synthetic image with a Gaussian-blurred patch pasted into a sharp image → the patch has
the top |z|. A uniform depth-of-field gradient → no strong outlier (seeded).

## Acceptance Criteria

- [ ] The planted blurred patch is top-ranked (seeded test).
- [ ] A smooth focus gradient fixture does not produce outliers beyond the documented level.
- [ ] Documentation explains the difference from PP-026 and the depth-of-field false positives.

## Expected Outcome

A cheap, interpretable visual consistency cue.

## Documentation Requirements

A detector page.

## Dependencies

**Blocked by** (merge these before starting): PP-072, PP-013.

**Integrates with** (start now; finish the integration once these land): PP-012.

**Work plan (two PRs).** PR 1, mergeable now: implement the algorithm as a pure NumPy function in the plugin's `algorithm.py` (scaffold it with PP-074), unit-tested with the PP-072 synthetic generators; reference this issue with `Refs`. PR 2, after PP-012 is merged: add the `processor.py` wiring and an integration test through the controller; this PR uses `Closes`.

## Priority and Effort

P2, small.

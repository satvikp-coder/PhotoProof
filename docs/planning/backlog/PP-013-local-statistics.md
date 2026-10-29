---
id: PP-013
title: "feat(imaging): add shared block tiling and robust local-anomaly statistics"
milestone: M2
labels: [type:feature, priority:P1, area:imaging, effort:small, status:blocked]
depends_on: [PP-012]
---
## Summary

Provide one shared, tested library for block tiling, local statistics, and robust
anomaly normalization (median/MAD z-scores). Every per-region detector uses it instead
of re-implementing it.

## Problem Statement

ELA, noise variance, sharpness, edge and grid detectors all compute per-block
statistics and then flag blocks that deviate from the image-wide distribution.
Duplicating that logic would produce subtly different normalizations that the fusion
step (PP-032) cannot reconcile.

## Objective

`photoproof.imaging.stats` with `tile`, `block_reduce`, and `robust_zscore`, plus a
standard `AnomalyMap` output structure.

## Technical Background

The robust z-score is `z = (x − median(x)) / (1.4826 · MAD(x))`, where
`MAD = median(|x − median(x)|)`. The factor 1.4826 makes MAD a consistent estimator of
σ for Gaussian data. It resists the very outliers (tampered regions) that a mean/stddev
normalization would absorb. Tiling uses non-overlapping or overlapping (stride < size)
blocks, with an explicit border policy.

## Functional Requirements

- `tile(array, block, stride, border="drop"|"pad_reflect") -> view` with block coordinates.
- `block_reduce(array, block, stride, func)` for mean, variance, median and MAD.
- `robust_zscore(values, mask=None)` with a defined result when MAD = 0: return zeros and
  flag `degenerate=True`. It must never divide by zero.
- `AnomalyMap` dataclass: `values` (float32, H_b × W_b), `block`, `stride`, `scale`
  (`robust_z`), `valid_mask`, `notes`. It is serializable to an artifact (NPZ or 16-bit
  PNG plus a JSON sidecar, documented).
- Upsampling to image resolution (nearest or bilinear) for visualization.

## Technical Implementation

Module `src/photoproof/imaging/stats.py`. Vectorised NumPy via
`numpy.lib.stride_tricks.sliding_window_view`. No new dependencies beyond NumPy.

## Integration Requirements

A library, not a plugin. Consumers: PP-015, PP-017, PP-019, PP-025, PP-026, PP-032.

## Security and Edge Cases

Images smaller than one block → empty map with a `too_small` note (never an exception).
Constant images (MAD = 0). NaN/inf in the input → rejected. Memory: windows are views,
so avoid materializing copies.

## Testing Requirements

Known-answer tests on synthetic arrays (a planted outlier block gets |z| > 5),
degenerate inputs, and border policies. A benchmark on a 12 MP image.

## Acceptance Criteria

- [ ] A planted outlier block in Gaussian noise gets the highest |z|, and others stay within ±3 in a seeded test.
- [ ] MAD = 0 returns zeros with `degenerate=True`.
- [ ] A 12 MP single-channel 16×16 block reduction completes in under 1 s on CI hardware (baseline recorded).
- [ ] `AnomalyMap` round-trips through its artifact serialization.

## Expected Outcome

Every regional detector speaks the same statistical language.

## Documentation Requirements

A plugin guide section on producing an `AnomalyMap`.

## Dependencies

PP-012 (NumPy and the image contracts).

## Priority and Effort

P1, small.

---
id: PP-018
title: "feat(plugins): extract noise residuals with wavelet-based denoising"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-072]
integrates_with: [PP-012]
---
## Summary

Plugin `noise.residual` computes the high-frequency noise residual `R = I − D(I)` using
wavelet denoising, as the shared input for noise-based detectors.

## Problem Statement

Sensor noise and post-processing leave statistical fingerprints. Spliced regions from
another camera, or processed differently, often have different noise characteristics.
The former prototype used a Gaussian blur residual, which mixes edges into the
residual.

## Objective

A reproducible residual artifact with documented denoiser parameters, consumed by
PP-019.

## Technical Background

Wavelet shrinkage (Donoho & Johnstone): decompose with a discrete wavelet transform
(e.g. db8 or sym4, 4 levels), soft-threshold the detail coefficients with a
noise-adaptive threshold, and reconstruct. The residual contains noise plus some edge
leakage. The Mihcak–Kozintsev–Ramchandran local Wiener filter in the wavelet domain is
the classic denoiser in PRNU literature (Lukáš, Fridrich & Goljan 2006). Implement
BayesShrink or the Wiener variant and document which.

## Functional Requirements

- Output `noise.residual` (artifact: float32 NPZ of the luminance residual; optional
  per-channel) and a summary (global σ estimate via MAD of the finest diagonal subband:
  `σ = median(|HH1|)/0.6745`).
- Config: wavelet, levels, method (`bayesshrink`|`wiener`), channel (`luma`|`rgb`).
- Deterministic.

## Technical Implementation

Add `PyWavelets` (MIT; wheels for all targets; hash-pinned). Package
`src/photoproof/plugins/noise/residual/`. Pad to wavelet-compatible dimensions and crop
back.

## Integration Requirements

Plugin ID `noise.residual`; priority 10; inputs `image.decoded` (unconverted colour);
outputs `noise.residual`, `noise.sigma_global`. Required by `noise.variance_map`
(PP-019). Optional at pipeline level.

## Security and Edge Cases

Memory (float32 copies of a 50 MP image are about 200 MB, so check limits). Images
smaller than 2^levels (reduce levels, record). Saturated or clipped regions have
near-zero noise (flagged later in PP-019).

## Testing Requirements

Add Gaussian noise of known σ to a smooth synthetic image → the estimated global σ is
within 10%. The residual of a noise-free gradient is near zero. Determinism.

## Acceptance Criteria

- [ ] Global σ estimate within ±10% of the injected σ for σ ∈ {2, 5, 10} (seeded tests).
- [ ] Residual artifact is deterministic and recorded with denoiser parameters in the manifest.
- [ ] Memory limit enforced for oversized inputs.

## Expected Outcome

A sound, reusable noise residual for noise-consistency analysis.

## Documentation Requirements

A detector page: mathematics, parameters, limitations (JPEG compression suppresses
noise, denoising in-camera, edge leakage).

## Dependencies

**Blocked by** (merge these before starting): PP-072.

**Integrates with** (start now; finish the integration once these land): PP-012.

**Work plan (two PRs).** PR 1, mergeable now: implement the algorithm as a pure NumPy function in the plugin's `algorithm.py` (scaffold it with PP-074), unit-tested with the PP-072 synthetic generators; reference this issue with `Refs`. PR 2, after PP-012 is merged: add the `processor.py` wiring and an integration test through the controller; this PR uses `Closes`.

## Priority and Effort

P1, medium.

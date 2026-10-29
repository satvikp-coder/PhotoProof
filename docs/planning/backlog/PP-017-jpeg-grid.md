---
id: PP-017
title: "feat(plugins): detect JPEG 8×8 compression-grid misalignment"
milestone: M2
labels: [type:feature, priority:P2, area:plugins, effort:medium, status:blocked]
depends_on: [PP-072, PP-013]
integrates_with: [PP-012]
---
## Summary

Plugin `compression.jpeg_grid` estimates the dominant 8×8 blocking-grid origin of the
image and maps regions whose blocking artefacts align to a *different* grid, which is
characteristic of content pasted from another JPEG or of cropping.

## Problem Statement

When a region is copied from another JPEG, its block boundaries rarely align with the
host's grid. This works in the pixel domain, so it applies even after the host is saved
as PNG. It is complementary to the DCT analysis (PP-016), which assumes an aligned grid.

## Objective

A global grid-origin estimate (dx, dy ∈ 0..7) with a confidence, plus a local map of
grid-alignment mismatch.

## Technical Background

Blocking Artifact Grid (BAG) extraction (Li, Yuan & Yu 2009): compute second-order
differences across pixel rows and columns, accumulate periodic energy at period 8, and
find the phase. Locally, compare the energy at the dominant phase to the best
alternative phase per window. Ye, Sun & Chang (2007) use similar blockiness measures.

## Functional Requirements

- Outputs `compression.grid_origin` (`{dx, dy, confidence}`) and `compression.grid_map`
  (AnomalyMap: higher = locally aligned to a different phase).
- Config: window size (default 64, multiple of 8), stride.
- Very weak blocking (high quality or no JPEG history) → `confidence: low` and an empty
  map, which is not negative evidence.

## Technical Implementation

`src/photoproof/plugins/compression/jpeg_grid/`, luminance channel of `image.decoded`.
Vectorised difference filters; periodicity via summed |∂²| at each phase.

## Integration Requirements

Plugin ID `compression.jpeg_grid`; priority 10; inputs `image.decoded`; outputs above;
optional. If PP-016 runs, the two should agree on the global origin. A disagreement is
recorded as an informational note (via fusion, not a cross-plugin dependency).

## Security and Edge Cases

Resampled or rescaled images destroy the grid (low confidence). Images smaller than
2 windows. Strong periodic textures at period 8 (false positive; document it).

## Testing Requirements

Generated JPEG cropped by (3, 5) pixels → estimated origin (3, 5). A splice of a
misaligned JPEG patch → highest-mismatch windows. A PNG with no JPEG history → low
confidence.

## Acceptance Criteria

- [ ] Global origin is estimated correctly for all 64 crop offsets on a generated q=80 fixture.
- [ ] A misaligned pasted patch is ranked top-1 in the local map (seeded test).
- [ ] Low-confidence cases produce an explicit `confidence: low` and are documented as non-evidence.

## Expected Outcome

A pixel-domain compression-history cue that survives format conversion.

## Documentation Requirements

A detector page with the method, references, limitations and false positives
(periodic textures, resizing).

## Dependencies

**Blocked by** (merge these before starting): PP-072, PP-013.

**Integrates with** (start now; finish the integration once these land): PP-012.

**Work plan (two PRs).** PR 1, mergeable now: implement the algorithm as a pure NumPy function in the plugin's `algorithm.py` (scaffold it with PP-074), unit-tested with the PP-072 synthetic generators; reference this issue with `Refs`. PR 2, after PP-012 is merged: add the `processor.py` wiring and an integration test through the controller; this PR uses `Closes`.

## Priority and Effort

P2, medium.

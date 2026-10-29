---
id: PP-015
title: "feat(plugins): implement Error Level Analysis as a supporting signal"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-072, PP-013]
integrates_with: [PP-012]
---
## Summary

Plugin `compression.ela` computes an Error Level Analysis map and a per-block robust
anomaly map. It is explicitly labelled a *supporting* signal that can never establish
forgery on its own.

## Problem Statement

ELA is popular and easy to misread: textures, edges, sharpening, resaving and
non-JPEG sources all produce bright regions. The former prototype (commit `bf90881`)
displayed raw ELA. The product must contextualize it.

## Objective

A reproducible ELA artifact plus an `AnomalyMap` (PP-013), with metadata that tells
fusion how weak and format-dependent the signal is.

## Technical Background

ELA: recompress the image at JPEG quality q (typically 90–95) and compute
`E = |I − JPEG_q(I)|` per channel, then aggregate (max or mean over channels). Regions
compressed a different number of times or at different qualities converge differently,
so their error levels differ. Limitations: high-frequency content naturally shows higher
error; repeated saves equalize levels; it is meaningless for PNG screenshots with no JPEG
history; and the choice of q changes the result.

## Functional Requirements

- Config (`schema.py`): `quality` (int 1–100, default 95), `aggregate` (`max`|`mean`),
  `block` (default 16), `edge_suppression` (bool, default true: divide by local gradient
  energy to reduce texture false positives, with documented behaviour).
- Outputs: `compression.ela_map` (artifact: 8-bit PNG of the scaled error, scale recorded)
  and `compression.ela_anomaly` (AnomalyMap of block means, robust-z).
- A summary scalar with `strength: weak`, plus the interpretation note "supporting
  evidence only".
- Applicability: JPEG (primary). For non-JPEG inputs the plugin runs and marks
  `context: no_jpeg_history`, and fusion must down-weight it.

## Technical Implementation

- `src/photoproof/plugins/compression/ela/`: processor uses Pillow in-memory JPEG
  encoding (an `io.BytesIO`, never disk), NumPy for the differences, and
  `imaging.stats` for blocks.
- Determinism: fixed encoder settings (`subsampling=0`, `optimize=False`); record the
  libjpeg version in `python_dependencies`/manifest.

## Integration Requirements

- Plugin ID `compression.ela`; priority 10; requires: none; inputs: `image.decoded`
  (unconverted colour, to avoid ICC artefacts); outputs as above.
- Failure behaviour: optional (`required: false`).
- Fusion (PP-033) must treat ELA as weak, and never let it alone exceed the "suspicious"
  threshold. That rule lives in PP-033 and must reference this plugin by ID.

## Security and Edge Cases

Very large images (memory for the recompressed copy; stay within limits). Grayscale and
alpha (convert, record). 16-bit (downconvert, record). Uniform images (degenerate MAD →
empty findings).

## Testing Requirements

- Synthetic splice: paste a q=60 JPEG patch into a q=95 image and resave at q=95 → patch
  blocks have the highest robust z (seeded test).
- False-positive fixture: a single-compression image with strong texture. Document the
  observed z-scores as the baseline.
- Determinism: identical output bytes across two runs.

## Acceptance Criteria

- [ ] The synthetic double-quality splice is ranked top-1 among blocks in the test.
- [ ] Output includes the "supporting evidence only" interpretation and `context` for non-JPEG inputs.
- [ ] The ELA artifact is deterministic for fixed settings.
- [ ] The documentation page lists at least five false-positive scenarios with examples.
- [ ] Baseline z-score distributions on authentic images are recorded (input for PP-037).

## Expected Outcome

ELA is available as one carefully caveated signal among several.

## Documentation Requirements

A detector page (`docs/plugins/`) with the mathematical basis, parameters, limitations,
false positives and applicable formats.

## Dependencies

**Blocked by** (merge these before starting): PP-072, PP-013.

**Integrates with** (start now; finish the integration once these land): PP-012.

**Work plan (two PRs).** PR 1, mergeable now: implement the algorithm as a pure NumPy function in the plugin's `algorithm.py` (scaffold it with PP-074), unit-tested with the PP-072 synthetic generators; reference this issue with `Refs`. PR 2, after PP-012 is merged: add the `processor.py` wiring and an integration test through the controller; this PR uses `Closes`.

## Priority and Effort

P1, medium.

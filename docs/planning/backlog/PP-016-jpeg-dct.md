---
id: PP-016
title: "feat(plugins): analyze JPEG quantization tables and DCT histograms for double compression"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:large, status:needs-design]
depends_on: [PP-012, PP-013]
---
## Summary

Plugin `compression.jpeg_dct` reads quantization tables and quantized DCT coefficients
directly from the JPEG bitstream, estimates primary quantization, and detects aligned
double JPEG compression (A-DJPG) locally.

## Problem Statement

Spliced regions often carry a different compression history. DCT-domain analysis is
far more principled than ELA, but it needs access to the coefficients, which
pixel-domain decoders discard.

## Objective

Global outputs (quantization tables, estimated libjpeg quality, camera/software
signature hints) and a per-8×8-block double-compression likelihood map.

## Technical Background

- After decoding and requantizing with step q2 a coefficient previously quantized with
  q1 ≠ q2, its histogram shows periodic peaks and gaps (the double-quantization effect).
  Fourier analysis of DCT histograms reveals the periodicity (Lukáš & Fridrich 2003;
  Popescu & Farid 2004).
- Lin et al. (2009) and Bianchi & Piva (2012) model per-block likelihoods of single vs
  double compression for localization.
- Quantization tables can be matched against libjpeg's standard tables scaled by quality
  to estimate Q; non-standard tables hint at specific cameras or software.

## Functional Requirements

- Extract Y/Cb/Cr quantization tables and Y-channel quantized coefficients.
- Global: `compression.jpeg_tables` (tables plus estimated quality and a
  `standard_table: bool` flag).
- Local: `compression.dq_map` (an `AnomalyMap` of per-block probability of double
  compression, using Bianchi–Piva-style mixture modelling over the first N AC
  frequencies).
- Applicability: JPEG, or JPEG reconstructed from JXL (PP-007). `NOT_APPLICABLE`
  otherwise.

## Technical Implementation

- Coefficient access: evaluate `jpeglib` (libjpeg bindings that expose DCT coefficients)
  versus a pure-Python Huffman decoder. Decide in the PR, considering wheel availability
  across 3 OS × 2 arch, licences and maintenance. Status: needs-design.
- Package `src/photoproof/plugins/compression/jpeg_dct/`. Config: AC frequencies used,
  EM iterations, minimum block count.

## Integration Requirements

Plugin ID `compression.jpeg_dct`; priority 10; inputs: `input.original_bytes`
(+ optional `image.reconstructed_jpeg`); outputs: `compression.jpeg_tables`,
`compression.dq_map`. Optional. Fusion treats it as medium-strength evidence.

## Security and Edge Cases

The bitstream parser handles attacker-controlled bytes, so it must run in the isolated
decoder worker (PP-009). Progressive JPEG, restart markers, 12-bit JPEG (reject),
arithmetic coding (reject unless supported), and tiny images (too few blocks → low
confidence).

## Testing Requirements

Synthetic single- and double-compressed JPEGs at known (q1, q2) pairs. Known-answer
quality estimation for libjpeg q ∈ {50, 75, 90, 95}. A localized double-compression
splice.

## Acceptance Criteria

- [ ] Estimated quality matches the libjpeg encoder quality within ±2 for standard tables on generated fixtures.
- [ ] Double compression with q1 < q2 is detected globally on generated fixtures (report detection rate over ≥ 50 seeded cases; baseline only, no invented threshold).
- [ ] The per-block map is produced, with a documented probability semantics.
- [ ] Non-JPEG inputs → `NOT_APPLICABLE`.
- [ ] Parser fuzzed via PP-061 without crashes.

## Expected Outcome

A principled compression-history detector with localization.

## Documentation Requirements

A detector page with the mathematics, references, limitations (q1 > q2 is hard, cropping
misaligns the grid, see PP-017) and false-positive scenarios.

## Dependencies

PP-012, PP-013. Uses PP-007 if available.

## Priority and Effort

P1, large. Needs a library decision first.

---
id: PP-069
title: "test(imaging): cross-format consistency testing across JPEG, WebP, HEIF and JPEG XL"
milestone: M6
labels: [type:testing, priority:P2, area:imaging, area:testing, effort:medium, status:blocked]
depends_on: [PP-003, PP-006, PP-007]
---
## Summary

Verify that decoding and normalization produce consistent working images across
formats, and measure how each detector's output depends on the container format for
equivalent content.

## Problem Statement

The same scene saved as JPEG, WebP, HEIF or JXL decodes to different pixels
(format-specific artefacts, colour handling, bit depth). Detectors tuned on JPEG may
misbehave on other formats, and decoders may disagree on orientation or colour.

## Objective

A format-consistency test suite and a report of per-detector behaviour by format, which
feeds each plugin's `supported_formats` declaration.

## Technical Background

Lossless round trips (PNG ↔ lossless WebP ↔ lossless JXL) must produce identical pixels.
Lossy formats are compared with PSNR/SSIM thresholds. Orientation and ICC handling must
agree across decoders for equivalent metadata.

## Functional Requirements

- Lossless round-trip identity tests for every format that has a lossless mode.
- Orientation and ICC consistency tests (the same metadata in each container).
- A detector-by-format matrix on equivalent content: output stability metrics, and
  recommendations for `supported_formats` per plugin.

## Technical Implementation

`tests/integration/test_cross_format.py` (pixels; runs in CI where codecs exist,
otherwise skips with a reason). A report generator in `tools/`.

## Integration Requirements

Updates the plugins' `supported_formats` via follow-up PRs, and the format-support table.

## Security and Edge Cases

Codec availability differs per platform, so tests skip explicitly with a reason and never
pass silently.

## Testing Requirements

The deliverable is tests and a report.

## Acceptance Criteria

- [ ] Lossless round trips are pixel-identical for every lossless-capable format (tests).
- [ ] Orientation and ICC behaviour is consistent across decoders (tests).
- [ ] The detector-by-format matrix is published, and `supported_formats` recommendations are filed.

## Expected Outcome

Format-independent correctness, and an evidence-based per-plugin format scope.

## Documentation Requirements

The format-support table and the results page.

## Dependencies

PP-003, PP-006, PP-007.

## Priority and Effort

P2, medium.

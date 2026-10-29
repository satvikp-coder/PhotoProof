---
id: PP-003
title: "feat(imaging): decode JPEG, PNG and WebP through a Pillow adapter"
milestone: M1
labels: [type:feature, priority:P1, area:imaging, effort:medium, status:ready]
depends_on: []
integrates_with: [PP-002]
---
## Summary

Implement the first `ImageDecoder` adapter, based on Pillow, for JPEG, PNG and WebP. It
enforces `DecodeLimits` before allocating pixels and preserves original bytes.

## Problem Statement

No decoder exists, so plugins cannot access pixels. JPEG, PNG and WebP cover most
practical inputs and the forensic datasets.

## Objective

`PillowDecoder.decode(data, source_sha256=..., limits=...) -> DecodedImage` returns
a validated 8-bit (or 16-bit for PNG) pixel buffer with format metadata, or raises
`InputRejectedError` for oversized, malformed or unsupported data.

## Technical Background

Pillow opens images lazily: `Image.open` parses headers, and `.load()` decodes pixels.
The header-level size check must happen between those two steps. Pillow's own
`Image.MAX_IMAGE_PIXELS` raises `DecompressionBombError` only above 2× the threshold
and warns below that. PhotoProof must set its own limit per call and turn warnings into
errors. Animated WebP has multiple frames; `max_frames` applies.

## Functional Requirements

- Supported: baseline and progressive JPEG, PNG (8/16-bit, palette, alpha), lossy and
  lossless WebP (still images; animated images rejected unless `max_frames > 1`).
- Width × height × frames is checked against `limits.max_pixels` from the header,
  before `.load()`.
- Exposes the ICC profile bytes and EXIF orientation *without* applying them
  (normalization is PP-004).
- `library_versions()` returns Pillow and libjpeg/libwebp versions (`PIL.features`).
- Never writes to disk and never modifies the input bytes.

## Technical Implementation

- Add the `Pillow` dependency: update `pyproject.toml`, regenerate both lockfiles with the
  documented `uv` commands, and record its licence (MIT-CMU) in the PR.
- Module `src/photoproof/imaging/adapters/pillow.py`. Register it in the decoder registry.
- Use `warnings.catch_warnings()` with `simplefilter("error", Image.DecompressionBombWarning)`.
- Convert to a documented working mode: `RGB` or `RGBA` (8-bit), `I;16` for 16-bit PNG.
  The conversion policy is recorded in `DecodedImage`.
- Error mapping: `UnidentifiedImageError`, `OSError` (truncated), `SyntaxError` →
  `InputRejectedError` with the format and a short reason.

## Integration Requirements

- Used by the decode step that PP-012 exposes to plugins.
- Decoder versions become part of manifests and cache keys (`software` and the
  per-plugin `python_dependencies`).
- Runs in-process for now. PP-009 moves decoding into an isolated worker without
  changing this adapter's interface.

## Security and Edge Cases

- Truncated JPEG: Pillow may decode partially with `LOAD_TRUNCATED_IMAGES`. It must
  stay **False**, so truncated input is rejected.
- Huge dimensions with a tiny file (decompression bomb) → rejected before `.load()`.
- CMYK and YCCK JPEGs: convert and flag, or reject, with a documented choice.
- Zero-dimension images → rejected.
- Timeouts: wall-clock enforcement comes with PP-009. Document the in-process gap.

## Testing Requirements

- Generate fixtures programmatically in tests (Pillow can encode), so no binary files
  are committed: progressive JPEG, 16-bit PNG, palette PNG, lossless WebP, animated WebP.
- Malformed corpus: truncated at several offsets, corrupted headers, a bomb header
  (e.g. 100000×100000 PNG IHDR with a minimal IDAT).
- Assert that original bytes are unchanged after decode.

## Acceptance Criteria

- [ ] Decodes JPEG, PNG and WebP fixtures to the documented working modes.
- [ ] Rejects every malformed fixture with `InputRejectedError`, with no partial decode and no crash.
- [ ] Rejects inputs above `max_image_pixels` before pixel allocation (verified with a header-only bomb).
- [ ] Pillow is hash-pinned in both lockfiles and CI passes on all three OSes.
- [ ] Decoder versions appear in manifests.

## Expected Outcome

PhotoProof can turn the three most common formats into a safe, validated working image.

## Documentation Requirements

Format-support table (`docs/planning/index.md`, then operations docs), and the security
model section on decoders.

## Dependencies

**Blocked by** (merge these before starting): nothing. This can start now.

**Integrates with** (start now; finish the integration once these land): PP-002.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P1, medium.

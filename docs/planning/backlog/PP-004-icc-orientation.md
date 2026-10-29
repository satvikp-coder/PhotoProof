---
id: PP-004
title: "feat(imaging): normalize ICC colour profiles and EXIF orientation into a working image"
milestone: M1
labels: [type:feature, priority:P1, area:imaging, effort:medium, status:ready]
depends_on: []
integrates_with: [PP-003]
---
## Summary

Create `imaging/normalization.py`, which turns a `DecodedImage` into a documented
working representation: sRGB, orientation applied, and an explicit record of every
transformation.

## Problem Statement

The same photograph can be stored rotated with an EXIF orientation flag, or in Display
P3 or Adobe RGB. Detectors that compare pixels across regions or images will produce
artefacts if colour spaces and orientation are inconsistent. Unrecorded transformations
also break traceability.

## Objective

A pure function `normalize(decoded, policy) -> WorkingImage` with a transformation log
that is recorded in the manifest.

## Technical Background

- EXIF orientation (tag 0x0112) values 1–8 encode rotations and mirrors.
- An ICC profile converts device values to the Profile Connection Space. Pillow's
  `ImageCms` (LittleCMS) converts profile → sRGB with a chosen rendering intent.
- Forensic caution: colour conversion alters pixel statistics. Compression and noise
  detectors (ELA, DCT, noise residuals) must be able to request the **unconverted**
  decoded data. Normalization therefore produces a view and never replaces the original.

## Functional Requirements

- `WorkingImage` carries: pixels, mode, width, height, `colour_space`
  (`srgb` | `source`), `orientation_applied: bool`, `transforms: list[str]`.
- Policy options: `apply_orientation` (default true), `convert_to_srgb` (default true),
  `rendering_intent` (`perceptual` | `relative_colorimetric`, default relative).
- Missing ICC → assume sRGB and record `assumed_srgb`.
- Invalid or corrupt ICC → do not convert, record `icc_invalid`, and continue.

## Technical Implementation

- Module `src/photoproof/imaging/normalization.py`, using `PIL.ImageCms`.
- Orientation transforms via `Image.transpose` with an explicit mapping table (no
  `ImageOps.exif_transpose` side effects on metadata).
- The transform log is a list of stable string codes for manifests.

## Integration Requirements

- PP-012 exposes both `image.decoded` (as stored) and `image.working` (normalized) as
  run-level contracts, so each plugin chooses explicitly.
- The normalization policy becomes part of the cache key (via settings or plugin
  config).

## Security and Edge Cases

- Malicious ICC profiles have caused LittleCMS CVEs. Catch errors, never crash, and
  keep decoding in the isolated worker once PP-009 lands.
- Very large embedded profiles (> 4 MB): ignore them and record that.
- Orientation values outside 1–8 → ignore and record.
- 16-bit inputs: convert in 16-bit where supported; otherwise record the depth
  reduction.

## Testing Requirements

- All eight orientations on a small asymmetric synthetic image, asserting pixel
  positions.
- Conversion from a synthetic Display P3 profile (Pillow can create profiles) produces
  values within ±1 of a reference.
- Corrupt ICC bytes → `icc_invalid`, no exception.

## Acceptance Criteria

- [ ] All eight EXIF orientations normalize correctly, as verified by pixel-position tests.
- [ ] ICC conversion to sRGB is applied and logged, and invalid profiles are handled without failure.
- [ ] The unconverted decoded image remains available to plugins.
- [ ] The transformation log appears in the manifest.

## Expected Outcome

Detectors operate on consistent pixels, and every transformation is traceable.

## Documentation Requirements

Plugin guide (choosing `image.decoded` or `image.working`), and the architecture data
flow.

## Dependencies

**Blocked by** (merge these before starting): nothing. This can start now.

**Integrates with** (start now; finish the integration once these land): PP-003.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P1, medium.

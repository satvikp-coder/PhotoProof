---
id: PP-006
title: "feat(imaging): implement HEIF and HEIC decoding"
milestone: M1
labels: [type:feature, priority:P2, area:imaging, effort:medium, status:blocked]
depends_on: [PP-003, PP-005]
---
## Summary

Add an `ImageDecoder` adapter for HEIF/HEIC (the iPhone default) using `pillow-heif`
(libheif bindings).

## Problem Statement

Much contemporary photographic evidence is HEIC. Without a decoder these inputs are
rejected.

## Objective

Decode still HEIF/HEIC images, including 10-bit, to the working representations of
PP-004 and PP-005, with explicit handling of auxiliary images and image sequences.

## Technical Background

HEIF (ISO/IEC 23008-12) is an ISO-BMFF container holding HEVC-coded items. A file can
contain a primary image, thumbnails, depth maps, alpha and gain-map auxiliary images,
and grid-tiled images. `pillow-heif` exposes the primary image and metadata. Licensing:
libheif is LGPL. HEVC decoding uses libde265 (LGPL) and has patent considerations. The
PR must include a licence note.

## Functional Requirements

- Decode the primary image only; record the count and type of auxiliary items in metadata.
- Preserve 10/12-bit depth (PP-005) and expose ICC/CICP and EXIF orientation.
- Reject image sequences (`.heics`) unless `max_frames` allows them.
- Codec availability: if `pillow-heif` has no wheel for the platform, the decoder is
  *unavailable* and reported by the `imaging.codecs` health check. This is not a crash.

## Technical Implementation

- Optional dependency group `heif` in `pyproject.toml`, included in the lockfiles once
  wheels exist for all supported OS/arch combinations. Otherwise use platform markers,
  and document the result.
- Adapter `src/photoproof/imaging/adapters/heif.py`. Header-level dimension check via
  `pillow_heif.open_heif(..., convert_hdr_to_8bit=False)` before decoding.

## Integration Requirements

Registered in the decoder registry for format `heif`. Library versions (libheif,
libde265) go into manifests. Must run inside the isolated decoder worker (PP-009) once
available.

## Security and Edge Cases

libheif has a history of memory-safety CVEs, so isolation (PP-009) is strongly
recommended before enabling the format by default. Other cases: grid images whose total
size exceeds limits; malformed `ftyp`; missing primary item; huge auxiliary images.

## Testing Requirements

Generate HEIF fixtures with `pillow-heif` in tests where encoding is available;
otherwise skip with a reason. Cover the malformed corpus via PP-058. Run the
cross-format comparison in PP-069.

## Acceptance Criteria

- [ ] An 8-bit and a 10-bit HEIC decode correctly, and the 10-bit depth is preserved.
- [ ] Unavailable codec → health warning plus `InputRejectedError("heif decoder unavailable on this platform")`.
- [ ] Oversized grid images are rejected before decode.
- [ ] Licence review is recorded in the PR and in `docs/security/index.md` (dependency section).
- [ ] `heif` is added to `formats.accepted` defaults only after PP-009 is merged.

## Expected Outcome

iPhone photographs can be analysed.

## Documentation Requirements

Format-support table, troubleshooting entry for missing codecs.

## Dependencies

PP-003, PP-005. Strongly recommended after PP-009.

## Priority and Effort

P2, medium. Blocked until the decoder and HDR representations exist.

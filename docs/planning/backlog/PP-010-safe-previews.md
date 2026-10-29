---
id: PP-010
title: "feat(imaging): generate safe previews with metadata removed"
milestone: M1
labels: [type:feature, priority:P2, area:imaging, area:security, effort:small, status:blocked]
depends_on: [PP-004]
---
## Summary

Generate bounded-size PNG or WebP previews of the working image, with all metadata
(EXIF, GPS, XMP, ICC beyond sRGB) removed, for display in WebKit and reports.

## Problem Statement

Showing the original in a browser exposes the browser's decoders to untrusted bytes and
can leak sensitive metadata such as GPS location in screenshots or exported reports.

## Objective

`make_preview(working_image, max_edge=2048) -> ArtifactRef` produces a re-encoded,
metadata-free preview stored as an artifact.

## Technical Background

Re-encoding from decoded pixels means the browser parses only PhotoProof-generated
bytes. Lanczos resampling suits downscaling. Previews are *derived artifacts*: they are
never evidence and must be labelled as such.

## Functional Requirements

- Longest edge ≤ `max_edge` (configurable), aspect ratio preserved, sRGB, 8-bit.
- No EXIF, XMP, IPTC or GPS data, and no ICC except an optional sRGB tag.
- Deterministic output for identical input and settings (fixed encoder parameters).
- Stored in `artifacts/` with `media_type` set; recorded in the manifest.

## Technical Implementation

Module `src/photoproof/imaging/preview.py`. Pillow `Image.resize(..., LANCZOS)`, then
`save(format="PNG", optimize=False)` with no `exif`/`pnginfo`. Optionally run as the
built-in support plugin `support.preview`.

## Integration Requirements

As a plugin: ID `support.preview`, priority 5, `requires` the decode step, input
`image.working`, output `support.preview`. WebKit (PP-039, PP-042) and reports (PP-046)
display only previews.

## Security and Edge Cases

Very small images (1×1); images with alpha (composite on a neutral background, or keep
alpha and document the choice); 16-bit inputs (tone-map via PP-005).

## Testing Requirements

Assert that previews contain no EXIF/XMP chunks, using a parser on the output bytes, and
that previews are byte-identical across two runs.

## Acceptance Criteria

- [ ] Previews never contain EXIF, GPS, XMP or IPTC data (verified by inspecting the output bytes).
- [ ] Preview generation is deterministic.
- [ ] Previews are labelled derived artifacts in manifests and never replace evidence.

## Expected Outcome

The UI and reports can safely show images without leaking metadata or exposing
browsers to raw evidence.

## Documentation Requirements

Security model (privacy), plugin contract table.

## Dependencies

PP-004.

## Priority and Effort

P2, small.

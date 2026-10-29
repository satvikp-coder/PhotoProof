---
id: PP-008
title: "feat(imaging): implement AVIF and optional TIFF decoding"
milestone: M1
labels: [type:feature, priority:P2, area:imaging, effort:medium, status:blocked]
depends_on: [PP-003]
integrates_with: [PP-005]
---
## Summary

Support AVIF stills, and TIFF as an optional format, through decoder adapters, with
strict limits on multi-page and tiled TIFF.

## Problem Statement

AVIF is common on the web, and TIFF is common in professional and scientific
workflows and in some datasets. TIFF is also notoriously complex and a frequent source
of decoder vulnerabilities.

## Objective

Decode AVIF primary images (8–12 bit) and single-page TIFF (8/16-bit; uncompressed,
LZW, Deflate, JPEG), and reject the risky TIFF variants unless explicitly allowed.

## Technical Background

AVIF is AV1 intra frames in HEIF/ISO-BMFF. Recent Pillow versions include AVIF support
(libavif); otherwise use `pillow-avif-plugin`. TIFF supports multiple IFDs (pages),
tiles, strips, many compressions, BigTIFF and private tags. libtiff has a long CVE
history.

## Functional Requirements

- AVIF: decode the primary item; reject sequences unless `max_frames` allows them;
  expose CICP metadata.
- TIFF: first IFD only by default. Reject BigTIFF, unusual compressions (e.g. JBIG,
  OJPEG) and more than `max_frames` pages, with clear messages.
- TIFF stays out of `formats.accepted` by default (opt-in).

## Technical Implementation

Adapters `imaging/adapters/avif.py` and `imaging/adapters/tiff.py`. Dimension checks
from the header (Pillow `Image.open` without `.load()`). The TIFF compression allowlist
is a constant.

## Integration Requirements

Registry formats `avif` and `tiff`. Codec versions go into manifests. Runs in the
isolated worker (PP-009).

## Security and Edge Cases

TIFF: IFD loops (offsets pointing backwards), very many IFDs, strip offsets beyond EOF,
huge declared dimensions. AVIF: grid items, alpha auxiliary items, huge declared
dimensions.

## Testing Requirements

Generated fixtures (Pillow can write TIFF; AVIF where an encoder is available).
Malformed TIFF corpus: IFD loop, out-of-range offsets, unsupported compression.

## Acceptance Criteria

- [ ] AVIF 8- and 10-bit stills decode correctly.
- [ ] TIFF decodes the first page for allowed compressions and rejects the disallowed variants with explicit messages.
- [ ] An IFD loop is rejected without hanging, verified by a test with a timeout.
- [ ] TIFF is disabled by default and documented as opt-in.

## Expected Outcome

Broader format coverage, without accepting TIFF's full attack surface.

## Documentation Requirements

Format-support table, and `formats.accepted` guidance in the settings reference.

## Dependencies

**Blocked by** (merge these before starting): PP-003.

**Integrates with** (start now; finish the integration once these land): PP-005.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P2, medium.

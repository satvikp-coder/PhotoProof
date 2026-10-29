---
id: PP-007
title: "feat(imaging): implement JPEG XL decoding"
milestone: M1
labels: [type:feature, priority:P2, area:imaging, effort:medium, status:blocked]
depends_on: [PP-003, PP-005]
---
## Summary

Add a JPEG XL decoder adapter (libjxl via a maintained Pillow plugin such as
`pillow-jxl-plugin`), including detection of losslessly recompressed JPEGs.

## Problem Statement

JPEG XL is increasingly supported, and it can store a legacy JPEG losslessly
(*JPEG reconstruction*). This matters for compression forensics: the original DCT
coefficients can be recovered.

## Objective

Decode JXL stills to working images, and report whether the file contains JPEG
reconstruction data. When it does, reconstruct the original JPEG bytes so that DCT
plugins (PP-016) can analyse them.

## Technical Background

JXL has two modes: VarDCT (lossy) and Modular (lossless). The `jbrd` box stores JPEG
bitstream reconstruction data, which allows a byte-exact original JPEG to be recovered.
Codestream and container signatures are handled by PP-002.

## Functional Requirements

- Decode the primary frame. Reject animations unless permitted by `max_frames`.
- Expose `jpeg_reconstruction_available: bool` and, when true, offer reconstruction as a
  separate derived artifact (never replacing the original evidence).
- Preserve high bit depth (PP-005).

## Technical Implementation

- Choose the binding in the PR, based on wheel availability for 3 OS × 2 arch and on
  maintenance status. Document the alternatives considered.
- Adapter `imaging/adapters/jxl.py`. Header dimension check before decode.

## Integration Requirements

Format `jxl` in the registry. New optional run-level contract `image.reconstructed_jpeg`
(an artifact reference) when available, which DCT plugins may declare as input.

## Security and Edge Cases

Run in the isolated worker (PP-009). Reconstruction data that does not match the
codestream must be rejected, never trusted. Also handle huge dimensions declared in a
small header.

## Testing Requirements

Round trip: encode a JPEG fixture to JXL with lossless recompression, decode it,
reconstruct, and compare the bytes to the original JPEG. Add malformed codestreams.

## Acceptance Criteria

- [ ] Lossy and lossless JXL stills decode to working images.
- [ ] Losslessly recompressed JPEGs are reconstructed byte-exactly in a test.
- [ ] Missing codec is reported by health checks and rejected cleanly.
- [ ] Licence (BSD-3 for libjxl) is recorded.

## Expected Outcome

JXL inputs are supported, and recompressed JPEG history is recoverable.

## Documentation Requirements

Format-support table, plugin contract table (`image.reconstructed_jpeg`).

## Dependencies

PP-003, PP-005.

## Priority and Effort

P2, medium.

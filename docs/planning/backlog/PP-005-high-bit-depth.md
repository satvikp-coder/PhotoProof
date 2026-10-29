---
id: PP-005
title: "feat(imaging): define high-bit-depth and HDR working representations"
milestone: M1
labels: [type:feature, priority:P2, area:imaging, effort:medium, status:needs-design]
depends_on: []
integrates_with: [PP-004]
---
## Summary

Specify and implement how images with more than 8 bits per channel (16-bit PNG/TIFF,
10/12-bit HEIF/AVIF, PQ/HLG HDR) are represented for plugins, without silently
quantizing to 8-bit.

## Problem Statement

Modern phones produce 10-bit HEIF and HDR content. Quantizing to 8-bit destroys
exactly the low-amplitude noise and residual statistics that forensic detectors rely
on, and doing it implicitly makes results irreproducible.

## Objective

A documented working-representation contract: dtype, value range, transfer function
and colour primaries, available to plugins as `image.working_hdr` when the source has
more than 8 bits.

## Technical Background

- Bit depth vs transfer function: 10-bit SDR (BT.709) differs from 10-bit PQ (SMPTE
  ST 2084) or HLG (ARIB STD-B67).
- Use `float32` linear-light or `uint16` code values. The trade-off: float32 is
  convenient for maths but is 4× the memory of 8-bit RGB.
- CICP (ITU-T H.273) metadata in HEIF/AVIF signals primaries, transfer and matrix.

## Functional Requirements

- `WorkingImage` supports `uint16` code values plus explicit `transfer`
  (`srgb`, `bt709`, `pq`, `hlg`, `linear`) and `primaries` (`bt709`, `p3`, `bt2020`).
- An 8-bit preview/working image is still produced, through a documented tone-mapping
  operator, for detectors that require it.
- The memory estimate is checked against `DecodeLimits` before conversion.

## Technical Implementation

- Extend `imaging/normalization.py` and `DecodedImage` with `transfer`/`primaries`.
- A design note in the PR comparing `uint16` and `float32` storage. Recommended:
  `uint16` storage, with float conversion done by the plugins that need it.
- Tone mapping: a simple, documented operator (e.g. BT.2390 EETF or Reinhard) with
  parameters recorded.

## Integration Requirements

New run-level contract `image.working_hdr` (present only when bit depth > 8). Plugins
declare it if they support it. This is a plugin API MINOR addition (1.x).

## Security and Edge Cases

- Memory exhaustion: 8K 16-bit RGB at float32 is about 400 MB. Enforce limits before
  conversion.
- Inconsistent CICP and ICC metadata: prefer CICP, record the conflict.
- Unknown transfer → treat as SDR and record `transfer_assumed`.

## Testing Requirements

Synthetic 16-bit PNG gradients (round-trip without loss); tone mapping is monotonic;
the memory-limit rejection path.

## Acceptance Criteria

- [ ] 16-bit PNG decodes to `uint16` without loss (round-trip test).
- [ ] `image.working_hdr` is present only for inputs with more than 8 bits and documents transfer and primaries.
- [ ] The 8-bit derivative is produced by a documented, deterministic operator.
- [ ] Memory limits are enforced before conversion.

## Expected Outcome

High-bit-depth evidence is preserved for the detectors that can use it.

## Documentation Requirements

The plugin guide contract table and the imaging section of the architecture docs.

## Dependencies

**Blocked by** (merge these before starting): nothing. This can start now.

**Integrates with** (start now; finish the integration once these land): PP-004.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P2, medium. Needs design agreement on representation before coding (status:needs-design).

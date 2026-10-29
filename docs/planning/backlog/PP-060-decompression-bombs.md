---
id: PP-060
title: "security(imaging): protect against decompression bombs and resource exhaustion"
milestone: M6
labels: [type:security, priority:P1, area:security, area:imaging, effort:medium, status:blocked]
depends_on: [PP-003, PP-009]
---
## Summary

Systematically enforce limits at every amplification point: compressed → decoded pixels,
metadata expansion, frame counts, intermediate float copies in plugins, and total per-run
memory. Document the resource budget.

## Problem Statement

Size limits on input bytes do not bound memory. A 50 KB PNG can declare 100k×100k pixels.
Plugins also create float32 copies (4–16× the 8-bit size). Several detectors running on
a large image can exhaust memory even with benign input.

## Objective

A resource budget model, `ResourceBudget.check(estimate)`, used by decoders and plugins
before large allocations, with consistent errors.

## Technical Background

Amplification factors: pixels × channels × bytes per sample × copies. Set the decode
ceiling from `resources.max_image_pixels` (default ≈ 179 MP, Pillow's default). Each
plugin estimates its peak from metadata (`resources_estimate(width, height) -> bytes`,
optional metadata), and the planner can mark plugins `NOT_APPLICABLE` with
`resource_limit` before running.

## Functional Requirements

- Decoders: a header dimension check (already required) plus a frame-count check plus a
  metadata size cap (EXIF/XMP/ICC ≤ configurable MB).
- Plugins: an optional `max_megapixels` metadata field. Larger inputs → the plugin works
  on a downscaled working image (if declared safe) or is `NOT_APPLICABLE` with a
  `resource_limit` reason.
- A run-level memory budget `resources.max_run_memory_mb` (enforced under
  `ProcessExecutor`; best-effort and documented in-process).

## Technical Implementation

`imaging/limits.py` (budget helper). A metadata schema addition (plugin API MINOR).
Executor applicability check.

## Integration Requirements

All decoders and detectors. Manifests record downscaling decisions.

## Security and Edge Cases

Animated images (frames × pixels). Huge ICC/EXIF. zlib bombs in PNG chunks. Multi-image
HEIF grids.

## Testing Requirements

Bomb fixtures from PP-058 are rejected before allocation (measure peak RSS in a
subprocess test where possible). A large benign image → plugins downscale or are
`NOT_APPLICABLE` as declared.

## Acceptance Criteria

- [ ] Bomb fixtures are rejected without exceeding 2× the declared budget (measured in a subprocess test on Linux).
- [ ] Plugins exceeding `max_megapixels` are downscaled or marked `NOT_APPLICABLE` with a recorded reason.
- [ ] The resource budget model is documented with worked examples.

## Expected Outcome

Predictable memory use under hostile and very large inputs.

## Documentation Requirements

Security model, settings reference, and the plugin guide (`max_megapixels`).

## Dependencies

PP-003, PP-009.

## Priority and Effort

P1, medium.

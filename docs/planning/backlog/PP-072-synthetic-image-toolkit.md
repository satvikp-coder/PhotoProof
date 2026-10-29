---
id: PP-072
title: "feat(imaging): add the NumPy image-array contract and synthetic forgery generators"
milestone: M1
labels: [type:feature, priority:P1, area:imaging, area:testing, effort:small, status:ready]
depends_on: []
---
## Summary

Add NumPy and Pillow, fix the array conventions every detector uses, and build a
seeded generator library that produces synthetic forgeries with exact ground-truth masks.
This lets all detector work start in parallel, before the decoding chain (PP-012) exists.

## Problem Statement

Every detector issue needs image arrays and labelled test images. Without a shared
toolkit, each developer either waits for the ingestion and decoding chain or invents
their own fixtures and conventions. That blocks most of the backlog, and the resulting
detectors would disagree on dtype, channel order and value range.

## Objective

`photoproof.imaging.arrays` (conventions plus validation) and `tests/fixtures/synthetic.py`
(generators), so each detector algorithm can be implemented as a pure function
`f(np.ndarray, config) -> result` and tested now.

## Technical Background

Working-array convention (matches what PP-012 will hand to plugins): `H × W × C`,
C-contiguous, RGB channel order, `uint8` for 8-bit or `uint16` for high bit depth;
grayscale as `H × W`. Algorithms convert to `float32` internally. Synthetic forgeries with
known masks are standard in the literature for unit-level verification (dataset
evaluation comes later in PP-066 and PP-067).

## Functional Requirements

- `imaging/arrays.py`: `validate_image(arr) -> ImageSpec` (shape, dtype, channels),
  `to_float32(arr)` (scales to [0, 1]), `to_luma(arr)` (BT.601 weights, documented), and
  read-only enforcement helpers.
- `tests/fixtures/synthetic.py`, all seeded and returning `(image, mask, meta)`:
  - `textured_scene(h, w, seed)`: natural-looking texture (sum of filtered noise plus
    shapes), so keypoints and noise statistics are realistic;
  - `copy_move(img, *, translate, rotate_deg, scale)`: source/target masks;
  - `splice(host, donor, region)`;
  - `noise_splice(img, sigma_in, sigma_out)`;
  - `blur_patch(img, sigma)`;
  - `jpeg(img, quality)` and `double_jpeg(img, q1, q2, region=None)` (Pillow, in memory);
  - `misaligned_jpeg_patch(img, offset)`;
  - `with_exif(img, **tags)` (Pillow).
- A pytest fixture `synthetic` that exposes the generators.

## Technical Implementation

- Add `numpy` and `Pillow` to `pyproject.toml` runtime dependencies. Regenerate all three
  lockfiles (CONTRIBUTING.md §1.8). Confirm wheels for Python 3.11–3.15 on all three CI
  OSes; the CI matrix proves this.
- `src/photoproof/imaging/arrays.py`; `tests/fixtures/synthetic.py`; tests in
  `tests/unit/test_synthetic.py`.
- Pillow is used here only for **encoding** test fixtures. Decoding untrusted input stays
  the job of PP-003 and PP-009.

## Integration Requirements

- Every detector issue (PP-013–PP-026, PP-028–PP-030) depends on this, and the malformed-file corpus
  (PP-058) reuses the Pillow helpers.
- PP-012 must hand plugins arrays that satisfy `validate_image`.

## Security and Edge Cases

Generators only process programmatic data, never external files. Validation rejects
non-contiguous, object-dtype, NaN-containing or zero-size arrays.

## Testing Requirements

Determinism (same seed → identical bytes). Mask correctness: the copy-move target region
equals the transformed source within interpolation tolerance. JPEG helpers produce
decodable JPEGs of the requested quality (check the quantization tables with Pillow).

## Acceptance Criteria

- [ ] numpy and Pillow are hash-pinned, and CI passes on 3 OSes × Python 3.11–3.15.
- [ ] Array conventions are documented in `docs/plugins/index.md`.
- [ ] All listed generators exist, are seeded and return exact masks (tests).
- [ ] At least one detector issue author has confirmed in the PR that the toolkit fits their needs.

## Expected Outcome

The whole team can start detector algorithms immediately, all on the same conventions.

## Documentation Requirements

Plugin guide (array conventions, how to test with synthetic forgeries), testing page.

## Dependencies

None. **Do this first;** it unblocks most of M2 and M3.

## Priority and Effort

P1, small.

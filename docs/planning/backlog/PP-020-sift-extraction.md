---
id: PP-020
title: "feat(plugins): implement SIFT-based copy-move keypoint extraction"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-012]
---
## Summary

Plugin `copymove.sift_keypoints` detects SIFT keypoints and computes descriptors on the
working image. It is the first stage of the copy-move chain:
PP-020 → PP-021 → PP-022 → PP-023 → PP-024.

## Problem Statement

Copy-move forgery duplicates a region within the same image, often scaled or rotated.
Keypoint methods are robust to those transforms, but need a carefully parameterized
extractor: too few keypoints miss small or flat duplicates, and too many slow down
matching quadratically.

## Objective

A deterministic set of keypoints `(x, y, scale, orientation, response)` with 128-D
descriptors, stored as an artifact for the matching stage.

## Technical Background

SIFT (Lowe 2004): a Difference-of-Gaussians scale space, extrema detection, sub-pixel
refinement, orientation assignment, and 4×4×8 gradient histograms. RootSIFT (Arandjelović
& Zisserman 2012: L1-normalize, then square root) improves matching. Low-texture regions
produce few keypoints, which is a known limitation (see PP-071).

## Functional Requirements

- Config: `max_keypoints` (default 20000, keeping the strongest responses),
  `contrast_threshold`, `edge_threshold`, `rootsift` (default true), `max_edge` (resize
  for detection, then rescale coordinates; default 3000 px; recorded).
- Output `copymove.keypoints` (artifact NPZ: float32 coordinates and descriptors) and a
  summary (count, density per megapixel).
- Fewer than 50 keypoints → `confidence: low` in the output (not an error).

## Technical Implementation

Add `opencv-python-headless` (Apache-2.0; hash-pinned). SIFT has been in core OpenCV
since 4.4. Package `src/photoproof/plugins/copymove/sift_keypoints/`. Convert to 8-bit
grayscale from `image.working`. Set `cv2.setNumThreads(1)` for determinism, and record
the OpenCV version.

## Integration Requirements

Plugin ID `copymove.sift_keypoints`; priority 10; inputs `image.working`; outputs
`copymove.keypoints`, `copymove.keypoint_stats`; optional. Required by PP-021.

## Security and Edge Cases

OpenCV is native code. Inputs are already-decoded arrays (not bytes), which lowers the
risk; process isolation for plugins comes with PP-052. Also handle huge images (resizing
is mandatory) and uniform images (zero keypoints → empty output, low confidence).

## Testing Requirements

Determinism across two runs on the same platform. Keypoint counts on a textured
synthetic image within a reasonable range. Coordinates rescale correctly with
`max_edge`. A zero-keypoint image.

## Acceptance Criteria

- [ ] Extraction is deterministic on a given platform (identical artifacts across runs).
- [ ] The `max_keypoints` and `max_edge` limits are enforced and recorded.
- [ ] Low-texture inputs produce an explicit low-confidence output, not an error.
- [ ] Runtime for a 12 MP image is recorded as a baseline.

## Expected Outcome

The keypoint foundation for copy-move detection.

## Documentation Requirements

A copy-move chain overview page describing all five stages, their contracts and
limitations.

## Dependencies

PP-012.

## Priority and Effort

P1, medium.

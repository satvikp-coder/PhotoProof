---
id: PP-026
title: "feat(plugins): analyze edge-boundary characteristics with Sobel and Canny"
milestone: M2
labels: [type:feature, priority:P2, area:plugins, effort:medium, status:blocked]
depends_on: [PP-012, PP-013]
---
## Summary

Plugin `visual.edges` characterizes object boundaries: edge width (the blur profile
across the edge) and gradient-magnitude statistics along Canny contours. It flags
boundaries that are abnormally sharp or abnormally soft relative to the image's typical
edges, as happens with cut-and-paste halos and feathering.

## Problem Statement

Splicing often leaves unnatural boundaries: hard aliasing, feather halos, or mismatched
edge sharpness between an object and its contact edges. Region sharpness (PP-025) does
not measure the boundary profile itself.

## Objective

A per-contour-segment edge-width estimate and an anomaly map highlighting boundary
segments that are inconsistent with the image's edge-width distribution.

## Technical Background

Detect edges with Canny (hysteresis thresholds derived from the median gradient). For
each edge pixel, sample the intensity profile along the Sobel gradient direction and
estimate the edge width as the 10–90% rise distance (equivalently the spread of the edge
spread function). Natural edges at the same depth share similar widths; pasted object
outlines often differ. Gradient direction comes from Sobel `atan2(Gy, Gx)`.

**How it differs from PP-025:** PP-025 measures blur *inside* blocks. This measures the
*transition profile across* detected boundaries, and is reported per contour segment.

## Functional Requirements

- Config: Canny `sigma` (auto thresholds ±33% around the median), profile length (default
  15 px), minimum segment length (default 20 px).
- Outputs `visual.edge_width_map` (sparse samples rasterized to blocks, AnomalyMap) and
  `visual.edge_segments` (JSON: polyline, mean width, robust z).
- Halo detection (optional): an overshoot/undershoot ratio in the profile.

## Technical Implementation

`src/photoproof/plugins/visual/edges/`; OpenCV Canny and Sobel; profile sampling with
bilinear interpolation (`map_coordinates` equivalent via `cv2.remap`).

## Integration Requirements

Plugin ID `visual.edges`; priority 10; inputs `image.working`; outputs above; optional,
weak in fusion.

## Security and Edge Cases

Very noisy images (spurious edges; pre-filter, recorded). Text and graphics (hard edges
are legitimate). JPEG ringing (overshoot, a halo false positive). Large images (cap the
sample count).

## Testing Requirements

A synthetic scene with consistent edge widths plus a pasted shape with hard (aliased)
edges → those segments are top-ranked. A JPEG at q=50 → the halo false-positive rate is
recorded as a baseline.

## Acceptance Criteria

- [ ] The pasted-shape boundary segments are top-ranked by |z| in seeded tests.
- [ ] Edge-width estimates on synthetic blurred step edges are within ±1 px of the known width.
- [ ] Documentation lists the JPEG-ringing and text/graphics false-positive scenarios.

## Expected Outcome

A boundary-focused cue complementary to region sharpness.

## Documentation Requirements

A detector page that includes the explicit comparison with PP-025.

## Dependencies

PP-012, PP-013.

## Priority and Effort

P2, medium.

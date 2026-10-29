---
id: PP-021
title: "feat(plugins): match SIFT descriptors with KNN and generalized Lowe ratio filtering"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-077]
integrates_with: [PP-020, PP-012]
---
## Summary

Plugin `copymove.matching` finds candidate duplicated keypoint pairs *within* the image,
using k-nearest-neighbour descriptor search and the generalized 2NN (g2NN) ratio test.

## Problem Statement

In copy-move detection a keypoint's nearest neighbour is often itself or an adjacent
keypoint. A plain Lowe ratio test (2NN) also cannot find a region copied multiple times.

## Objective

A set of candidate pairs `(i, j, distance)` that passes the g2NN test and spatial
constraints, ready for clustering.

## Technical Background

- KNN search with FLANN (KD-trees for SIFT) or brute-force L2 for small sets.
- Lowe ratio: accept when `d1/d2 < τ` (τ ≈ 0.6–0.8).
- g2NN (Amerini et al. 2011): sort the k neighbour distances `d1..dk`; accept neighbours
  `d_i` while `d_i / d_{i+1} < τ`. This supports multiple copies.
- Exclude self matches and pairs closer than `min_spatial_distance` (e.g. 10 px): the
  same structure, not a copy.

## Functional Requirements

- Config: `k` (default 10), `ratio` (default 0.5 for g2NN as in Amerini; recorded),
  `min_spatial_distance` (default 10 px), `max_pairs` (default 50000, strongest first).
- Output `copymove.matches` (artifact NPZ: index pairs, distances) and a summary (count).
- Symmetric duplicates (i,j)/(j,i) are deduplicated.

## Technical Implementation

`src/photoproof/plugins/copymove/matching/`. Use OpenCV FLANN with a fixed random seed
and checks, or brute force below N=5000 keypoints. Record the matcher choice in the
output.

## Integration Requirements

Plugin ID `copymove.matching`; priority 20; requires `copymove.sift_keypoints`; inputs
`copymove.keypoints`; outputs `copymove.matches`. Required by PP-022.

## Security and Edge Cases

Memory O(N·k). Approximate search non-determinism (fix the seeds; document that results
may differ across OpenCV versions, which the cache key already accounts for). Zero or
very few keypoints → empty matches, success.

## Testing Requirements

Synthetic copy-move (a textured patch copied with a translation) → most matches share
the planted displacement. Multiple copies of the same patch are all matched (g2NN). An
authentic textured image → match count recorded as a baseline.

## Acceptance Criteria

- [ ] At least 80% of matches on the planted translation fixture have the planted displacement ±2 px (seeded).
- [ ] Two copies of the same patch are both recovered (g2NN behaviour).
- [ ] Self matches and pairs closer than `min_spatial_distance` never appear.
- [ ] Deterministic output for fixed configuration on one platform.

## Expected Outcome

High-quality candidate pairs for geometric analysis.

## Documentation Requirements

The copy-move chain page: matching parameters and their trade-offs.

## Dependencies

**Blocked by** (merge these before starting): PP-077.

**Integrates with** (start now; finish the integration once these land): PP-020, PP-012.

**Work plan (two PRs).** PR 1, mergeable now: implement the algorithm as a pure NumPy function in the plugin's `algorithm.py` (scaffold it with PP-074), unit-tested with the PP-072 synthetic generators; reference this issue with `Refs`. PR 2, after PP-012 is merged: add the `processor.py` wiring and an integration test through the controller; this PR uses `Closes`.

## Priority and Effort

P1, medium.

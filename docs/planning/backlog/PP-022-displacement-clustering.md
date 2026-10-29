---
id: PP-022
title: "feat(plugins): cluster matched keypoints by displacement vectors"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-021]
---
## Summary

Plugin `copymove.clustering` groups candidate matches into coherent clusters of
source → target correspondences. Each cluster is a hypothesis for one copied region.

## Problem Statement

Raw matches mix true duplications with accidental matches across the image. Geometric
verification (PP-023) needs groups of matches that plausibly share one transformation.

## Objective

A list of clusters, each with its member pairs, its source and target keypoint sets, and
summary statistics, with spurious singletons removed.

## Technical Background

For pure translations all pairs share a displacement `v = p_j − p_i`, so clustering
displacement vectors (e.g. DBSCAN in v-space) works. For rotation and scaling the
displacement varies across the region, so cluster jointly in source and target location
space. Amerini et al. (2011) use agglomerative hierarchical clustering (Ward linkage) on
keypoint coordinates and keep cluster pairs connected by at least 3 matches. Implement
the hierarchical method as the default and DBSCAN on displacement as an alternative.
Document which handles which transforms.

## Functional Requirements

- Config: `method` (`hierarchical`|`dbscan`), `cut_threshold` / `eps`,
  `min_matches_per_cluster` (default 4, because RANSAC for an affine transform needs 3).
- Normalize the direction of each pair so that (A→B) and (B→A) collapse.
- Output `copymove.clusters` (JSON list: `id`, `pair_indices`, `source_bbox`,
  `target_bbox`, `mean_displacement`, `size`).

## Technical Implementation

`src/photoproof/plugins/copymove/clustering/`. Use `scipy.cluster.hierarchy` (BSD;
hash-pinned) or a NumPy DBSCAN. Record the choice and its dependency.

## Integration Requirements

Plugin ID `copymove.clustering`; priority 30; requires `copymove.matching` (plus the
keypoints via the matching outputs, or requires both); outputs `copymove.clusters`.

## Security and Edge Cases

Hierarchical clustering is O(n²) in memory. Cap the input pairs (`max_pairs` from
PP-021) and fall back to DBSCAN above a threshold, recording the fallback. No matches →
empty list.

## Testing Requirements

Fixtures: translation-only copy, rotated (30°) copy, scaled (0.8×) copy, two independent
copies → expected cluster counts. Random matches → no cluster above the minimum size
(seeded).

## Acceptance Criteria

- [ ] Planted translation, rotation and scale copies each yield one dominant cluster with the correct source and target boxes (IoU ≥ 0.5 against the planted boxes).
- [ ] Two independent copies yield two clusters.
- [ ] Random-match input yields no clusters of the minimum size in seeded tests.
- [ ] Memory guard and fallback are tested.

## Expected Outcome

Structured copy-move hypotheses ready for geometric verification.

## Documentation Requirements

The copy-move chain page: clustering, and its relationship to RANSAC.

## Dependencies

PP-021.

## Priority and Effort

P1, medium.

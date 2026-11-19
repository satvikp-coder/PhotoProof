# ADR-023: Bounded copy-move candidate clustering

## Decision

Register `copymove.clustering` after extraction and matching. Read actual artifacts
through the broker and validate original source SHA-256, working dimensions and
keypoint count. Publish canonical version-2 `Clusters` JSON and diagnostic stats.
This branch depends on PR #163's #77/#20/#21 contracts; dependent-branch success
does not establish integration on main.

Normalize direction by lexicographic working `[x,y]` endpoint order. Remove symmetric
duplicates while retaining original matching-row references. These source/target
roles do not establish historical copy direction. Authoritative cluster size is
`len(pair_indices)`; the companion summary exposes `{id,size}` without adding a
conflicting count to the shared interchange.

## Algorithms

Default Ward linkage uses the squared-distance Lance-Williams update on six features:
source `[x/W,y/H]`, target `[x/W,y/H]` and displacement `[dx/W,dy/H]`. The
dimensionless cut defaults to 0.5; it is a heuristic, not a probability. Fixed
row ordering resolves ties. This is actual Ward linkage, not greedy centroid merging.

Spatial grouping alone can admit random correspondences. An all-member least-squares
affine coherence screen rejects rank-deficient groups or reprojection RMSE above
the configured 3-pixel default. It does not execute RANSAC, publish verified
transforms or establish robust inlier identities. Groups containing outliers can
be rejected: that conservative recall limitation remains. Stage #23 still needs
robust geometric verification.

Ward can split a dense real region into adjacent fragments. A fixed graph of
the original group boxes joins fragments only when both source and target are
within `merge_gap_px` (default 16 pixels) and the combined all-row fit stays
coherent. A single deterministic edge pass avoids repeated quadratic rescans.
Distant regions with the same motion remain separate. No missing rows or
inliers are invented by a merge.

For rotation, a target point-cloud box can omit the transformed corners of the
source point envelope. Project that source envelope through the coherence fit,
include observed target points, and clamp the resulting target box to the working
frame. Record the policy explicitly. This is a candidate extent estimate, not a
verified transform or dense mask. The source extent still needs adequate features.

Alternative weighted DBSCAN uses working-pixel `[dx,dy]`, radius `eps` (default
0.5 pixels), exact-duplicate compression and neighborhood spatial bins. Border
points are density-reachable. It primarily handles translations; rotation/scale
can fragment its groups. Resource fallback does not claim Ward's transform coverage.

## Resource limits

Reject more than 50,000 pairs before clustering allocation. Ward runs up to
`ward_limit` (default 512, maximum 1,024), so its float64 distance matrix is at
most 8 MiB. This is not a whole-process memory guarantee. Larger inputs use DBSCAN
and record `ward_input_limit`. No pairs are silently truncated. Neighborhood
comparisons and coherence/merge row visits share a work budget (default 5 million,
maximum 10 million). At most 1,000 original groups and at most 499,500 group-edge
checks are permitted; the summary records each work component separately. At most
1,000 hypotheses are allowed. Budget failures raise errors, never successful
empty/partial results. Native plugin OS isolation remains #52.

## Compatibility and verification

The old unregistered processor expected inline arrays and a different output.
Version 2 publishes `copymove.clusters` with canonical original parent indices,
exclusive working-pixel boxes and mean `[dy,dx]`. Reanalyse from verified originals;
do not relabel historical artifacts. Host API 1.7 supplies the shared models and
artifact capability; the application release version is unchanged.

Tests preserve the seeded null that exposed spurious groups and the actual
SIFT/controller fixture that exposed over-fragmentation. They cover known
translation/rotation/scale correspondences, independent regions, exact linkage,
direction deduplication, resource guards, source/frame consistency, empty
diagnostics and repeatable artifacts. These are implementation fixtures, not
real-image accuracy evaluation. Clusters establish neither authenticity nor forgery.
Actual generated-pixel controller tests include 30-degree rotation, 0.8 scaling,
and two independently copied patches, with box IoU at least 0.5. They do not
establish general accuracy or prevent genuine repetitive patterns from matching.

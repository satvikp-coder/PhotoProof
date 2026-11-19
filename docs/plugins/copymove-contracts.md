# Copy-move interchange

The canonical models are in `photoproof.contracts.copymove`. The historical
`photoproof.plugins.copymove.contracts` module re-exports these models. Importing
that module does not select a legacy wire format. Extraction and matching use the
version-2 models through the actual controller. Clustering now consumes their
artifacts and emits candidate hypotheses. Verification and mask processors still
require migration; their presence alone does not establish a
working verified copy-move chain.

## Common fields and coordinates

Every model has these fields. Unknown fields are rejected.

| Field | Meaning |
|---|---|
| `contract_version` | Literal `2.0.0`; older artifacts require explicit migration or reanalysis. |
| `source_sha256` | Lowercase SHA-256 of the immutable original input bytes. |
| `image_size` | Working-image `[width,height]`, positive bounded integers. |
| `coordinate_space` | Literal `working_pixels`, after host EXIF normalization. |
| `calibration` | Literal `uncalibrated`; heuristic scores are not probabilities. |

Coordinates `xy` are pixel centres `[x,y]`. Boxes are integer
`[y0,x0,y1,x1]` with exclusive upper bounds. Displacements are `[dy,dx]`.
Consumers must obtain artifacts through `ctx.artifact`, compare the source hash
to `image.geometry.source_sha256`, and compare dimensions to the actual working
frame. A valid artifact hash alone does not establish these semantic relationships.
Array buffers are copied and backed by immutable bytes; caller mutations cannot
change a validated model and the writeable flag cannot be re-enabled.

## Keypoints

`Keypoints.to_npz()` contains numeric NPY members `metadata`, `xy`, `scale`,
`angle`, `response` and `desc`. `metadata` is a one-dimensional uint8 UTF-8 JSON
encoding of the common fields. Arrays have these shapes:

| Field | Type and meaning |
|---|---|
| `xy` | float32 `[N,2]`, finite coordinates inside the working frame. |
| `scale` | float32 `[N]`, positive keypoint diameter in working pixels. |
| `angle` | float32 `[N]`, finite orientation in degrees. |
| `response` | float32 `[N]`, finite detector response, used for strongest-first ordering. |
| `desc` | float32 `[N,128]`, finite SIFT descriptors, aligned with all other rows. |

Zero features is a successful empty diagnostic, not evidence of authenticity.
SIFT uses one native thread, clips input intensities to 0–255, and converts RGB
to grayscale. Detection resizing uses the actual rounded dimensions on each axis.
Pixel-centre mapping is `(coordinate+0.5)/axis_scale-0.5`; diameter uses the
area-equivalent inverse square-root scale, and angle is transformed by both axis
scales. The summary records the matrix, detection size and intensity policy.
Fewer than 50 features yields LOW feature-availability confidence. This confidence
does not measure forgery likelihood.

## Matches

`Matches.to_npz()` contains `metadata`, `pairs` and `distance`.
Metadata adds `num_keypoints`, the actual parent row count. `pairs` is int32
`[M,2]`, with every index in `[0,num_keypoints)`. `distance` is float32 `[M]`,
finite and nonnegative. Empty matches are allowed. The matching processor removes
self/nearby pairs, uses the documented group-gap ratio rule, removes symmetric
duplicates and caps the ordered eligible pairs. Correspondences are candidates;
repetitive genuine structures may also match.

## Clusters

`Clusters.to_json()` includes common fields, `num_matches` and a `clusters` list.
Each `Cluster` contains `id` (unique nonnegative integer), `pair_indices` (unique
indices into the actual matches), `source_bbox`, `target_bbox`, and
`mean_displacement` (finite `[dy,dx]`). Boxes stay within the working frame and
displacements must fit its dimensions. Collections are limited to 1,000 clusters.
An empty collection is valid.

The registered clustering stage reads actual keypoint/match artifacts and checks
the host source/frame. It uses bounded Ward linkage in normalized source, target
and displacement space, with an all-member affine coherence screen. Larger inputs
fall back to bounded displacement DBSCAN and record that limitation. Configuration,
fallback, rejected-group counts and per-cluster `{id,size}` are in
`copymove.cluster_stats`; size is derived from authoritative `pair_indices`.
See [ADR-023](../planning/adr-023-candidate-clustering.md) for thresholds, resource
budgets, direction conventions and recall limitations. These clusters have not
undergone robust RANSAC verification and are not strong forensic evidence.

## Verified transforms

`Verified.to_npz()` contains `metadata` and float64 `matrices` `[T,2,3]`.
Metadata adds unique `cluster_ids` and `transforms`. Each record has `cluster_id`,
`inliers` (positive count), `rmse` (nonnegative pixels), `scale` (positive),
`rotation_deg` (finite degrees), `score` (uncalibrated unit-range heuristic), and
`flags` (at most 16 strings of at most 128 characters). The corresponding matrix
maps source `[x,y,1]` to target `[x,y]`; its linear part must be finite and
nonsingular. Every transform references a known cluster. At most 1,000 transforms
are allowed; no verified transforms is valid. These types validate records, but
do not themselves execute RANSAC or prove that an inlier count is correct.

## Mask and regions

`Mask.to_npz()` contains `metadata` and uint8 `mask` `[height,width]`.
Labels are 0 background, 1 source and 2 target. Metadata adds `regions`: at most
1,000 records with `kind` (`source` or `target`), `bbox`, and positive `area`.
Area equals the number of that region's label pixels inside its box. Every
positive pixel must be covered exactly once by the region records.

`Mask.to_png()` produces an 8-bit grayscale semantic label PNG without original
metadata. It must travel with the JSON sidecar from `Mask.to_json()`; PNG alone
does not encode source identity or region semantics. `Mask.from_png()` validates
that sidecar, the PNG header, dimensions, single-frame grayscale mode and labels.
It reads bounded derived mask bytes in-process; it is not an OS sandbox for
untrusted original-image decoding. Overlapping source/target roles require a
different future contract and cannot be silently encoded as one label.

## Resource limits, fixtures and migration

Numeric archives accept only the exact member schema, reject pickle/object arrays,
duplicate or path-like members, and enforce a 64 MiB encoded/expanded ceiling,
1 MiB metadata ceiling and 16,777,216 elements per array. JSON rejects duplicate
keys and NaN/infinity. Empty numeric diagnostics are explicitly supported; the
default score-map reader still rejects empty evidence.

`tests.fixtures.copymove_golden.golden_examples(seed)` uses the actual #72 synthetic
image generator and known translation to build source-bound examples of all five
models. Its transform is labelled synthetic ground truth, not a claimed RANSAC
result. Round-trip tests cover NPZ, JSON and semantic PNG, invalid parent references,
wrong source/frame, immutable arrays and resource bounds.

Version 1 and the old packed `[x,y,scale,angle,response]` NPZ are incompatible.
Preserve historical artifacts and manifests. Reanalyse from verified originals,
or implement an explicit audited converter that establishes source identity and
working geometry. Never relabel historical bytes as version 2. Summary consumers
must read the declared artifacts instead of duplicated bulk lists; the matching
reference is `matches_artifact`. Host API 1.7 and plugin versions 2.0 declare this
change; the application release version is unchanged.

A single Windows/Python 3.12.14/OpenCV 4.14.0 measurement of the SIFT function on
seed-20 uniform RGB noise, 4000×3000 pixels, took 3.403 seconds with one native
thread, `max_edge=3000` and `max_keypoints=20000`. It produced 20,000 features.
This excludes imports, ingestion, decoding and other plugins. It is a baseline
measurement on one host, not an accuracy result, memory measurement or runtime
guarantee. Cross-platform or OpenCV-version byte identity is not promised.

On the same stack, an unmodified seed-21 uniform RGB noise image at 512×384
produced 599 SIFT features and zero candidate matches with defaults (`k=10`,
`ratio=0.5`, minimum distance 10, maximum pairs 50,000). This is one synthetic
negative baseline; it does not estimate false-positive rates on real photographs.

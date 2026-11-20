# Copy-move interchange

The canonical models are in `photoproof.contracts.copymove`. The historical
`photoproof.plugins.copymove.contracts` module re-exports these models. Importing
that module does not select a legacy wire format. Extraction and matching use the
version-2 models through the actual controller. Clustering now consumes their
artifacts and emits candidate hypotheses. Verification now publishes seeded
geometrically consistent transforms and exact inlier rows. Dense localization now
consumes those actual verified artifacts through the controller. Geometric and pixel
similarity remain heuristic evidence; independent fusion and real-image evaluation
are separate requirements.

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


## Geometric verification and bindings

`copymove.verified` is canonical `Verified` NPZ. `copymove.verified_inliers` is
canonical `Clusters` JSON with actual original match-row indices retained by
native RANSAC and the final residual check. Identical oriented coordinate pairs
count once. Counts equal selected rows; source/frame/parent agreement is mandatory.
No accepted model means empty typed diagnostics and a null maximum heuristic score.

`copymove.verification_bindings` uses frozen `VerificationBindings` (API 1.8),
inheriting all common fields and adding:

| Field | Meaning |
|---|---|
| `kind` | Literal `verification_bindings`. |
| `keypoints_sha256`, `matches_sha256`, `clusters_sha256` | Exact upstream-byte hashes, not reserialized-model hashes. |
| `verified_sha256`, `inliers_sha256` | Exact resulting artifact-byte hashes. |
| `geometric_model` | `similarity` or `affine`. |
| `reproj_threshold` | Final residual limit in working pixels. |
| `min_inliers` | Minimum distinct coordinate-pair support. |
| `seed` | Host context seed; details record effective native seeds. |
| `ransac_stop_confidence` | Native sampling termination, not an image probability. |
| `algorithm` | Literal `opencv_ransac`. |
| `correspondence_policy` | Literal `distinct_oriented_xy_pairs`. |
| `score_kind` | Literal `geometric_heuristic`; calibration remains `uncalibrated`. |

Use `verify_payload(kind,bytes)` before interpreting a referenced payload, then
validate its typed source/frame/count/geometry semantics. JSON is bounded to 1 MiB,
numeric archives to 64 MiB. Hashes establish byte consistency, not authentication.
`copymove.verification_details` is bounded JSON (`schema` identifies verification
details, `format_version=1`), binding the binding-artifact digest and recording
per-cluster results or rejection reasons. `copymove.verification_stats` holds
compact counts, work, actual configuration/seed/version and artifact references.

The score is distinct inliers divided by original candidate rows, times
`exp(-rmse/reproj_threshold)`. It is not calibrated. Actual-pixel autocorrelation
can flag translation-aligned repetitive structure and cap score; missing texture
or check coverage is explicit. Naturally repeated content can still match.
Rotation follows working image axes, positive clockwise. Configuration supports
`model`, `reproj_threshold`, `min_inliers`, `max_iterations`, `scale_min/scale_max`
(or the mutually exclusive `scale_range: [minimum,maximum]` alias),
`max_shear_deg`, sampling confidence, texture controls and work budgets.
See [ADR-024](../planning/adr-024-geometric-inlier-provenance.md) for exact limits
and caveats. Independent fusion and real-image evaluation remain separate requirements.
Historical prototype outputs require reanalysis.


## Dense localization artifacts

`copymove.mask` is the canonical 8-bit label PNG, paired with the exact
`copymove.mask_metadata` JSON from `Mask.to_json()`. Decode with
`Mask.from_png(png_bytes, metadata=sidecar_text, source_sha256=original_hash)`.
Source/target labels are deterministic geometric roles, not evidence of which patch
was copied first. The sidecar contains one aggregate bounding envelope and actual
pixel area per present role; it does not claim rectangular manipulation.

`copymove.regions` is separate bounded JSON: schema
`photoproof.copymove.dense-regions`, `format_version=1`, original hash, working
`image_size`, `coordinate_space=working_pixels`, `calibration=uncalibrated`, and
hashes of the PNG, sidecar, correlation, validity and verification bindings.
`regions` records each connected component's `kind`, half-open `[y0,x0,y1,x1]`
box, `area_pixels`, outer ring and holes. Rings contain `[x,y]` pixel centers;
degenerate thin components can have fewer than three vertices. These are raster
boundary descriptions, not continuous polygon areas. The PNG remains authoritative.

`copymove.correlation_map` is finite float32 NPY `[height,width]`, raw signed ZNCC
in `[-1,1]`. It is a similarity map, not a probability or normalized forensic
score. Its mandatory boolean companion `copymove.correlation_validity` has the
same dimensions. Untested, undefined flat, transparent, interpolation-border,
too-small-displacement and ambiguous-role pixels are false; stored zeros there
must not become negative evidence. Scores transported to source coordinates use
nearest-neighbor sampling; when transforms overlap, the greatest available
correlation is retained. Existing fusion needs a native adapter that validates the
manifest and validity before constructing an EvidenceLayer.

No verified transforms produce empty labels and all-invalid correlation. Summary
status `no_copy_move_found` means no copy-move detected by this method. A failed
provenance/budget check instead fails the plugin and publishes no mask outputs.
Use #66 metrics directly on `(mask.mask != 0)` for union localization; evaluate
source/target separately if role correspondence matters. Controller tests decode
the actual PNG and pass it to `tools.metrics.iou`.

### Migration

Mask plugin 2.0 replaces prototype raw inline matches/decoded coordinates and NPZ
mask output. Preserve historical results and reanalyse; do not relabel them.
Replace old `window_size`, `threshold`, `min_cluster_size` settings with documented
ZNCC/dilation/morphology settings. There is no automatic format conversion.
Existing host API 1.8 capabilities suffice; no additional host capability is added.

# ADR-024: Geometric verification and inlier provenance

## Decision

Read actual broker-verified artifacts, validate source/frame/parent counts, and run
OpenCV RANSAC with `ctx.seed`, one native thread, explicit iteration/termination
controls and ten refinement iterations. Each effective native seed is
`(seed + cluster_id*2654435761) & 0x7fffffff`. Sampling termination confidence is
unrelated to image authenticity.

Use clustering's lexicographic working `[x,y]` direction. Identical oriented
coordinate pairs count once: the lowest original matching-row index represents
duplicate SIFT orientations. Minimum six inliers therefore requires distinct
geometric correspondences. Keep only native-classified rows within the final
matrix's residual threshold. Reject insufficient, collinear, collapsed, trivial
identity and unavailable models explicitly. Both singular-value scales must fit
the configured range; check column shear and determinant. Flag affine reflection.
Rotation is positive clockwise in working image axes (y increases downward).

## Typed outputs

Publish canonical `Verified` NPZ and `Clusters` JSON at
`copymove.verified_inliers`, containing actual original match-row IDs. Every count
agrees with its selected row set. Boxes are inlier source envelopes and projected
target envelopes; they are not dense manipulation masks.

Host API 1.8 adds frozen `VerificationBindings`, hashing the exact upstream
keypoint/match/candidate bytes and resulting verified/inlier bytes. Check one
bounded payload at a time, then validate typed source/frame/count/geometry
semantics. Byte consistency is not authentication or proof an algorithm ran.
This prevents interpreting row IDs against another same-image/same-count archive.
Details JSON binds this document and records matrices, ratios, reasons and actual
seeds; summaries remain compact. Verified collections reject duplicate cluster IDs.
Preserve historical prototypes and reanalyse; application release version stays unchanged.

## Texture and scores

`score = distinct_inliers/original_candidate_rows * exp(-rmse/reproj_threshold)`
is an uncalibrated geometric heuristic. Absent/rejected models have no transform
and null diagnostic scores. Nothing reports authenticity or a forgery probability.

For near-pure translations, inspect actual source pixels with bounded linear FFT
autocorrelation: copy/sample the ROI, normalize intensity, use RGB luminance
(ignore alpha), subtract the mean, zero-pad and normalize lags by overlap energy.
Unobservable lags stay unavailable. Require a local peak at least 0.85, a preceding
valley and translation-phase alignment. A displacement-variance shortcut is
insufficient. `possible_repetitive_structure` caps score at 0.25; insufficient
texture/check coverage is explicit and caps at 0.1/0.5. Rotation/scale repetition
is not assessed by this policy. Genuine windows, tiles and crowds remain a
false-positive concern requiring #67/#68 evaluation and independent fusion.

## Bounds and integration

Limit pairs to 50,000 and estimated pair-iteration work to 50 million by default
(maximum 100 million). Texture ROI edge defaults to 128 (maximum 256); estimated
FFT work defaults to 64 million (maximum 256 million). Reject budget exhaustion
before the relevant native operation; never return partial/empty success instead.
These are algorithm bounds, not OS isolation or whole-process RSS guarantees (#52).

This branch depends on #166/#163. Local success is not main integration. #24 still
needs verified dense masks and #32 new-wire normalization. Tests cover seeded
parameter recovery, exact original-row outliers, real-pixel periodic/null cases
and actual controller rotation/scale; they do not establish general forensic accuracy.

# ADR-019: Preserve native robust-z anomaly semantics

## Status

Adapter implemented and unit-tested against actual compression algorithm records.
The native integration branch now connects these adapters through the production
controller and binary consumers. Full regression/CI and independent main-integration
review remain required. This decision does not provide calibration evidence.

## Problem

Existing detector output includes explicit AnomalyMap dictionaries, not just raw
arrays. ELA and JPEG-grid outputs contain values, boolean validity, block/stride
and robust-z scale; JPEG-DCT wraps this record under `anomaly`. Standardizing these
values again changes a detector's native statistic based on unrelated blocks.
Treating raw keypoints, feature counts, quantization tables or residual magnitude
as spatial forgery maps would also misrepresent diagnostic context.

## Decision

Read explicit robust-z records and preserve measured validity and native sampling.
Producer block/stride order is [height,width]; SamplingGrid uses [x,y]. The existing
algorithms start at decoded pixel zero; their adapter records that origin explicitly.
Reject unsupported scales, nonfinite measured values, nonboolean masks, mismatched
shapes and cells outside the source image. Empty/all-invalid measurements yield no
layer. Invalid cells use zero fill with validity=false; measured zero anomalies
remain available zero scores with validity=true. Source identity accompanies the
layer, with the binary writer/reader enforcing its format and expected geometry.

For nonnegative anomaly magnitude t, provisional z0>=0 and k>0, define
L(t)=1/(1+exp(-(t-z0)/k)) and s(t)=(L(t)-L(0))/(1-L(0)). Clip to [0,1].
This is monotonic, zero-anchored and bounded, with saturation for extreme values.
It is an **uncalibrated heuristic**, not a probability. No threshold or detector
strength gains empirical validity from this transformation.

An upper-tail mapping uses max(z,0). A two-sided mapping uses abs(z), selected
explicitly for measurements where both increased and decreased values matter.
Noise consistency needs both signs because local smoothing can reduce noise;
an adapter must not silently discard that negative deviation. Detector policy,
strength and reliability will be documented per actual producer before activation.
Raw sigma, JPEG tables and keypoint arrays require separate contextual treatment;
they are not accepted by this robust-z adapter.

The native-input reader recognizes actual ELA/grid records, the nested JPEG-DCT
anomaly record, and the persisted noise-consistency NPY plus its companion
`noise.variance_summary` sampling/validity metadata. Noise scores come through
`ctx.artifact`, not the duplicated legacy JSON values. Missing companion metadata
is unavailable; it never licenses a shape-based grid guess. Noise summary format2 is now compact: source hash/decoded size, explicit grid shape,
block/stride/scale and artifact references. A separate boolean validity NPY is read
through the exact declared nested selector; no duplicated numerical grids remain.
The direct consistency output must match the companion artifact reference.
Noise reliability must also be present. Low-confidence native noise measurements
are downgraded to weak context, with the reason retained; they cannot establish
sufficient non-weak coverage. Configured strength is a maximum, with no quality-based
promotion. Missing reliability is unavailable rather than presumed usable.

## Verification and migration

Unit checks consume actual ELA, JPEG-DCT and JPEG-grid algorithm records generated
from deterministic synthetic pixels/JPEG bytes. Additional checks establish native
score independence from unrelated blocks, monotonicity, signed behavior, measured
zero versus unavailable data, malformed records and finite saturation. These tests
also verify exact declared artifact selection and unavailable companion metadata.
The artifact-reader unit tests use a controlled context; they do not establish new
controller integration or replace the existing host broker security checks. These tests
do not measure forensic accuracy. Actual controller artifact transport, reliability
and source-coordinate integration must be completed before the coordinated v3
normalizer/rules/assessment/regions/heatmap migration. Historical scores retain
their old mapping and must not be relabeled with this definition.


## Verified dense copy-move adapter

The #24 producer now consumes actual verified inlier rows and publishes canonical
PNG labels with an exact Mask sidecar, signed correlation, boolean validity and
a hash-bound region manifest. The local normalization adapter reads all these
artifacts, verifies their manifest digests and source/working frame, then checks
verification binding digests and selected-row counts. Missing companions and
all-invalid correlation yield unavailable evidence, never fabricated clean maps.
Nonfinite/corrupt/contradictory payloads fail visibly.

Positive labelled pixels use max(ZNCC,0) times the minimum verified geometric
support across transforms. Other measured pixels have a measured zero indicator;
unmeasured pixels remain invalid zero fill. This is a provisional heuristic, not
a probability. Default geometric support floor0.8 and configured strength cap
never override an upstream caution. Known RANSAC model markers and duplicate-row
cleanup are informational; unknown markers, reflection, repetitive/insufficient
texture or other cautions downgrade the aggregate layer to weak. A mixture of
well-supported and cautioned transforms is conservatively downgraded as a whole;
per-transform reliability attribution is not claimed by the aggregate raster.

Working-pixel maps are reoriented exactly into decoded pixels for the existing
SamplingGrid wire using the authoritative signed-permutation EXIF matrix. No
resize, interpolated score or shape-based coordinate guess is used. The layer
retains localized_search_scope: tested candidate windows do not provide whole-image
negative coverage. Native producer/consumer activation is described in ADR-020.

Host API1.9 adds validate_pixel_transform and decoded_from_working plus the new
fused-score/validity transport. It follows the integrated-chain API1.8 requirement;
it does not change the release-managed application version. All eight EXIF inverse
roundtrips and budget-before-coordinate-allocation behavior have focused regressions.

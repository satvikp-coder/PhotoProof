# ADR-019: Preserve native robust-z anomaly semantics

## Status

Adapter implemented and unit-tested against actual compression algorithm records.
Production normalization/fusion wire migration and controller wiring are pending.
This decision does not complete PP-032 or provide calibration evidence.

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
is unavailable; it never licenses a shape-based grid guess. The legacy noise
summary still duplicates bulk values and must become compact during the coordinated
producer migration; this adapter does not resolve that producer's output-size limit.

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

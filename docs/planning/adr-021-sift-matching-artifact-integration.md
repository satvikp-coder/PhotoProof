# ADR-021: Connect SIFT extraction to verified candidate matching

## Decision

Read the actual declared `copymove.keypoints` artifact through the host broker.
Decode only the exact numeric NPZ member schema (`keypoints`, `descriptors`),
with ZIP member count/names, compression, expanded bytes, NPY headers and array
sizes checked. Never extract files or load object/pickled arrays. Numeric feature
archives may explicitly contain zero-row arrays; missing/empty score evidence
continues to be rejected by the default NPY reader.

Use the matching plugin's own validated configuration: k, ratio, minimum spatial
distance and maximum pairs. OpenCV self-matching receives explicit query and train
descriptors. Retrieve at most k+1 neighbors (including self), remove self/nearby
structures before ratio testing, and sort equal distances by index for stable ties.
Choose the first positive distance gap satisfying d_i < ratio*d_(i+1), retaining
the whole preceding candidate group. This permits multiple identical copies without
dividing zero by zero or selecting only a self match. No gap means no acceptance.
Deduplicate symmetric pairs, order by distance/index and apply the pair cap after
spatial eligibility. Approximate FLANN search uses a fixed seed and recorded OpenCV
version; cross-version/platform identity is not promised.

This explicit group-gap policy supplies candidate correspondences, not a calibrated
forgery score. Repetitive genuine structures can still match. Geometric verification,
valid localization and benchmark evaluation remain separate requirements.

## Compatibility and scope

The existing runtime packed arrays remain float32[N,5] keypoint rows
[x,y,scale,angle,response] in normalized working coordinates and float32[N,128]
descriptors. Matches remain int32[M,2] pairs plus float32[M] distances. Do not silently
interpret the separate typed contract-model serialization as this packed wire format;
that reconciliation remains explicit integration work.

Plugin version 2 removes duplicated bulk arrays from extraction/matching summaries
and replaces incorrect matching artifact aliases with `matches_artifact`. Consumers
read the arrays from their recorded immutable artifacts. Historical manifests retain
their original fields. This needs a breaking PR title and migration notes. Host API
1.7 adds bounded numeric archive reads and opt-in empty diagnostic arrays; application
release version remains unchanged.

## Verification

Actual controller tests decode a deterministic generated PNG, extract SIFT, consume
the stored artifact and recover both pasted copies. At least 80% of candidate motions
match the planted translations within two pixels. Configured parameters reach the
result; self/nearby and symmetric duplicates are excluded; repeated fixed-platform
matching yields identical pairs/distances. A flat fixture produces successful empty
matching without an authenticity verdict. Negative tests reject malformed schemas,
duplicate/path-like members, oversized expansion before decompression, bad CRC,
object arrays, nonfinite descriptors and invalid native-call parameters. These are
implementation fixtures, not an authentic-image accuracy benchmark. Clustering,
verification and masks still require their own artifact integration.

# ADR-027: Bounded reference-to-target registration

## Status

Pure algorithm stage for issue28 on an unmerged feature branch. This package has no
METADATA and is not a runnable controller plugin yet. Controller wiring, source-bound
artifact transport and downstream registration-failure handling follow in stage2.
Do not describe a working comparison chain before those integrations land on main.

## Decision

Estimate a forward matrix from reference working-pixel centers to target working-pixel
centers. Both inputs remain user-supplied material. Registration establishes a geometric
relationship, not provenance or which input should be trusted.

Use the existing OpenCV/NumPy dependencies. Extract SIFT with the copy-move stage's
contrast0.04, edge10 and RootSIFT conventions. Work images have at most1600 pixels on
their longest edge by default (hard2048). Feature admission defaults4000, hard8000.
Resize coordinates are restored with the exact half-pixel convention. Mutual two-neighbor
ratio0.75 matches are reduced to unique reference and target coordinates, so duplicate
orientations at a physical keypoint do not inflate geometric support.

Seed bounded RANSAC for similarity, affine or homography. Recompute actual target-pixel
residuals rather than trusting an estimator mask. Admission needs at least12 inliers,
ratio0.25, maximumRMSE2pixels and support hulls spanning at least0.5percent of each
frame. Check normalized-frame conditioning, projective poles, projected footprint and
reflection; reject reflections by default. These are provisional engineering controls.
If homography fails, an explicitly flagged affine candidate must independently pass
all checks. No unaligned or identity fallback is created on failure.

Optional ECC refines an already valid alignment. It operates on at most1,048,576 target
pixels with at most100 iterations. Convert the returned inverse residual warp back
into reference-to-target coordinates, then recheck geometry and inlier error. Failed,
resource-limited or worsening refinement retains only the already validated initial
alignment, with explicit refinement status. It never converts a failed registration
into an unaligned comparison.

Warp reference pixels bilinearly into the target frame. Coverage requires full opaque
source donors, opaque target pixels and continuous inverse coordinates inside the
reference; a separately warped validity image and exact coordinate checks prevent
interpolation rounding from manufacturing border coverage. Unmeasured pixels have
zero storage fill with validityfalse, never zero-difference evidence. Inputs are never
modified and returned arrays are read-only.

## Resource and interpretation limits

Admit at most4,194,304 pixels per input and8,388,608 combined before OpenCV copies.
These ceilings, bounded features/iterations and row-chunked inverse projection bound
numerical work, not process RSS or OS isolation. Native plugin process isolation
remains issue52. No byte decoding, file access, new dependency, model or dataset is
introduced in this algorithm.

Low texture, repeated patterns, non-planar scenes, parallax and unsupported viewpoint
changes can make registration fail or limit spatial reliability. Inlier quality does
not guarantee alignment away from measured features. Downstream difference maps must
retain overlap and registration limitations, and cannot turn differences into a forgery
finding by themselves. Stage2 must preserve source identities and actual transform
quality in manifests and make failed registration unavailable downstream.

## Validation

Seeded issue72 scenes with planted similarity/affine/homography transforms plus noise
had maximum corner errors0.036327/0.007402/0.021052pixels for seed28 and
0.037965/0.009124/0.023756 for seed29. Tests also cover unrelated/flat inputs, explicit
failure without pixels, source immutability, deterministic ECC, failed refinement,
alpha/border validity, different resolutions, allocation admission and invalid config.
Fault-injection controls test estimator fallback separately from precision fixtures.
These synthetic regressions do not establish real-image registration accuracy.

## Primary references

- [OpenCV robust transformation estimation](https://docs.opencv.org/4.13.0/d9/d0c/group__calib3d.html).
- [OpenCV image warp conventions](https://docs.opencv.org/4.13.0/da/d54/group__imgproc__transform.html).
- [OpenCV ECC refinement](https://docs.opencv.org/4.13.0/dc/d6b/group__video__track.html).

# ADR-025: Verified dense copy-move localization

## Decision

Consume broker-obtained keypoints, matches, candidates, verified transforms,
original-row inliers and VerificationBindings. Verify every payload digest,
original source, working frame, parent counts, candidate membership, distinct
correspondences and final residuals before dense image work. Hash consistency
is not authentication or proof that a geometric hypothesis means manipulation.

Warp a bounded source grayscale ROI into a target ROI around the actual target
inlier convex hull, with a margin for dilation and correlation windows. Compute
signed dense local ZNCC using box means, requiring complete interpolation/pixel
support and nonzero variance in both windows. Scale finite samples before luma
and correlation to avoid overflow; do not mutate the host arrays. Use cubic
intensity interpolation and nearest-neighbor label/validity transport. RGB and
grayscale are supported; RGBA excludes any non-opaque pixels, including source
interpolation footprints and complete ZNCC neighborhoods.

Retain the issue defaults: window 7, threshold 0.9, hull dilation 32 pixels and
morphology kernel 3. Euclidean distance-transform dilation avoids radius-squared
kernel work. Open then close thresholded pixels and retain components overlapping
the original inlier hull. Reapply validity after morphology. Exclude displacement
under 10 working pixels and remove pixels with both source/target roles because
the canonical label wire cannot express both. Record their count explicitly.
No copying chronology is inferred from the deterministic lexicographic roles.

## Limits and traceability

Before dense allocation admit at most four million frame pixels, 64 transforms,
and a configured sum of frame-plus-correlation-ROI pixel work (32 million by
default, hard maximum 64 million). Individual source and target ROIs also stay
within four million pixels. These are bounded admission controls, not an RSS
limit, an OS sandbox or a performance benchmark. Native OpenCV uses one thread.
Exhaustion fails rather than returning a partial or empty successful result.

Canonical label PNG and exact Mask JSON travel together. A separate versioned
regions manifest binds those and signed correlation/boolean validity NPY artifacts
to verification bindings and source/frame identity. Component pixel areas are
measured from the raster; pixel-center outer/hole rings describe boundaries.
The raster is authoritative, including degenerate thin contours. Metadata is
bounded to 1000 components, 2000 rings, 65536 vertices and one MiB, checked before
unbounded Python boundary-list serialization. All payloads are prepared before
writing output references.

No transform, no supported texture and no above-threshold component are distinct
observable situations. Empty labels use `no_copy_move_found`, meaning no copy-move
detected by this method; unavailable correlation records a reason and all-invalid
validity. A successful empty mask never establishes authenticity. Verification
texture/repetition flags remain in the regions manifest. Neither geometric nor
correlation agreement is calibrated forensic confidence. Fusion must inspect
validity, provenance and these cautions before treating a mask as evidence.

## Baselines and limitations

Initial Windows/Python 3.12 default unit baselines using the #72 copy_move generator:
fully textured seeded translation union IoU 0.937509; textured_scene seed24 with a
large constant shape: translation 0.668478, 30-degree rotation 0.461104, scale0.8
0.677018. The latter includes genuinely undefined flat ZNCC windows; retain this
limitation rather than fill them or tune thresholds. Initial actual-controller
seed22 patch baselines: translation 0.889136, rotation 0.535473, scale0.8 0.719522.
Tests retain and print these scenarios; final validation records any differences.
These synthetic cases are regression evidence, not a real-world accuracy claim.
Pillow rotation ground truth labels its whole expanded target rectangle, including
fill corners; dense correlation does not claim those corners as copied texture.

SIFT/clustering/verification can miss a copy before localization runs. Natural
repetition can yield coherent regions. A high pixel correlation cannot determine
whether repeated content was edited. Independent fusion, calibration and licensed
real-image evaluation remain separate work.

## Compatibility

Plugin 2.0 replaces prototype raw-match/decoded-space wiring and NPZ labels.
The canonical PNG sidecar uses the existing V2 Mask contract; regions manifest
format1 is separate. Reanalyse historical inputs without altering their evidence.
Old window_size/threshold/min_cluster_size configuration is rejected. Priority50
follows the registered priority40 verifier. Host API1.8 already supplies every
required artifact/image capability; no new host capability is introduced.

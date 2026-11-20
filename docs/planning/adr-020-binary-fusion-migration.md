# ADR-020: Coordinate binary fusion and measured coverage

## Status

Activated on the unmerged native-integration feature branch. Actual image ingestion,
isolated decoding, detectors, binary normalization/fusion, coverage assessment,
segmentation and source-matched heatmaps run through the controller. Full regression,
security/CI and independent review gates remain required before main integration.
This is not a calibration or real-image accuracy claim.

## Decision

Migrate producers and consumers together with explicit plugin version/migration
notes and a breaking PR title. Existing manifests retain their recorded versions
and score semantics. Do not enable a binary producer while leaving a JSON-grid
consumer behind. Host helper availability requires the next additive plugin API
after 1.8.0 (API1.9); the application release version remains release-managed.

Normalized layers carry source identity, explicit decoded sampling, canonical
binary scores, boolean validity, uncalibrated semantics and documented strength.
Actual native anomaly records use ADR-019. Diagnostic tables, keypoints, counts,
raw residuals and absolute sigma are context, not localized forgery evidence.
Unavailable detectors remain unavailable. Availability records must distinguish
actual failure, inapplicability and not-planned states from the host snapshot.
Low-reliability measurements can only weaken configured strength, never promote it.

Fusion projects measured footprints through actual image geometry onto a uniform
working-image grid. Preserve the finest measured spatial resolution; do not
collapse all maps to the coarsest detector or let a global scalar select the grid.
If the required grid exceeds the bounded allocation ceiling, fail visibly rather
than silently discarding spatial precision. Global weak context cannot create
localization or measured spatial coverage.

The new fused-score transport records source SHA-256, working dimensions, grid
shape, explicit uniform-working-cell coordinates and uncalibrated semantics.
Float32 scores and boolean validity are separate NPY artifacts. The initial ceiling
is 4,194,304 grid cells. Metadata cannot request resolution above the working image.
This ceiling bounds these numerical payloads, not the entire process's RSS.
Consumers verify source/dimensions against actual geometry before reading arrays;
declared artifact selectors provide the only byte access. Missing/all-invalid
evidence produces no fused map. A measured zero remains distinct from unavailable
zero fill, and invalid cells cannot contain a nonzero hidden score.

## Fusion policy to implement

Each independent family contributes at most once per pixel. Strong verified
spatial evidence combines by maximum; medium cues require either a provisional
high score or credible independent non-weak support. Mere positivity is insufficient:
small baseline scores must not mutually enable medium cues. Introduce an explicit
provisional corroboration floor, initially 0.5, and exclude zero-weight cues from
votes. Weak cues only boost already accepted non-weak evidence; same-family cues
cannot boost a family's strong contribution twice. Document provisional weights
and thresholds without accuracy or probability claims.

Keep per-detector accepted-contribution masks as bounded artifacts, not unbounded
JSON lists of every suspicious pixel. Segmentation must attribute retained regions
using those actual masks and preserve provenance across cleanup. It must not fill
unmeasured areas into supported localization. Region metadata remains capped, with
explicit truncation; the underlying mask retains all valid retained components.

Assessment counts usable independent non-weak measured coverage, respecting low
reliability, effective configuration and actual applicability. Diagnostic outputs
and duplicate outputs are not separate required detector families. Failed required
coverage yields inconclusive. Comparison differences require the actual comparison
contract and never establish forgery by themselves. No authenticity verdict exists.

Heatmaps and overlays use source-matched working coordinates, verified derived
previews and validity-aware transparency. They must not regenerate corrupt previews
or open originals. Preserve the uncalibrated legend and artifact traceability.

## Required verification before activation

Use actual controller runs with real compression/noise producers, plus deterministic
fixtures for spatial transforms, incomplete coverage and verified copy-move inputs.
Test orthogonal dimensions, flips/rotations, partial borders, gaps, measured zeros,
large artifacts, wrong-source/corrupt payloads, low-confidence noise, disabled weights,
low-positive independent cues, same-family correlation and region attribution after
cleanup. Preserve originals and report every unavailable path honestly. Copy-move
masks cannot gain strong status from bare binary data. The #24 producer now reads
actual verified inlier artifacts; the new local adapter checks its manifest,
validity and geometric reliability before applying the configured strength cap.
This branch activates the native producer and downstream consumers together;
main integration remains pending review and CI.
These checks establish implementation behavior, not validated forensic accuracy.


## Activated wire and policy

Normalization3 emits binary EvidenceLayer format2 records and fusion.availability
format1. The latter binds the original hash, actual host producer states, explicit
unavailable reasons, quality downgrades, native sampling and context-only diagnostics.
Native mappings are only ELA anomaly (weak), DQ anomaly (medium), JPEG-grid anomaly
(weak), noise consistency (medium with reliability cap), and verified dense mask
(strong only within its support/reliability cap). Tables, counts, raw residual/sigma
and summaries remain context; they cannot become one-cell coverage or localization.
Malformed supported data fails visibly; missing, unsupported or unread values emit
no layer. Global weak context cannot create measured coverage.

The root mapping file now contains only those five actual native policies. Native
robust-z mappings require robust_z with finite z0>=0 and k>0; mask similarity requires
identity and no robust-z parameters. Mask minimum_geometric_support defaults to0.8
and can only be strengthened. A configured class can only be lowered. These floors
and weights are provisional engineering controls without calibration evidence.

Fusion3 preserves the finest measured stride in a uniform working grid, validates
all source/frame metadata and excludes disabled weights from corroboration. An
independent non-weak raw score at least0.5 can corroborate medium evidence. Low
positive baselines cannot mutually enable evidence. Families contribute once;
strong scores combine by maximum, while weak cues only boost accepted non-weak
support. Source/fused allocation budgets fail rather than silently coarsening.
Accepted contributions use the new format1 source/working-grid record: separate
NPY scores and measured masks, class/family/effective weight, spatial/global scope,
notes and unavailable names. No per-pixel coordinate lists enter JSON.

Host snapshot version1 adds required_prior_outputs as an additive API1.9 field.
It lists only required producers earlier in the actual execution plan, not future
reporting/visualization work. Missing required measurements or unknown required
status force inconclusive. Optional not-applicable methods remain visible as
limitations; they never count toward the provisional independent-family minimum.
Sufficient non-low measured families can support an indicated category. A negative
category additionally needs enough non-candidate-window families; conditional
copy-move windows alone cannot support a full-frame no-indicator conclusion.
RunStatus still reports pipeline health independently of this heuristic assessment.

Segmentation3 clips every cleanup step to measured validity, retains attribution
from original hysteresis components when opening removes a high seed, and rejects
positive regions without spatial contribution references. Pixel-center boundary
rings retain holes; thin contours are marked degenerate, not replaced by fictional
rectangles. Metadata is capped at configured region count,4096 vertices per region
and16384 total; the complete retained raster survives metadata omissions, with
explicit counts and truncation. Small regions and scores remain provisional.

Preview2 binds original source, working dimensions and PNG digest while continuing
to remove original metadata. Heatmap3 accepts only this bounded derived artifact,
never originals or a regenerated corrupt preview. It checks source/frame/grid
alignment, samples exact uniform cell centers, and renders unmeasured cells
transparent even with display threshold zero. The legend binds score/validity/preview
hashes and records missing or truncated boundary metadata. Missing fused evidence
produces no PNG and an explicit unavailable legend.

Initial branch controller baselines: a seed22 copy on a384x512 image completed the
whole pipeline with union localization IoU0.889255 in both orientation1 and EXIF6.
Original bytes remained unchanged; every rendered artifact carries the expected
source/frame chain. These synthetic regressions do not validate forensic accuracy.


## Auditable region explanations

Region metadata version3.1 adds per-detector accepted-cell counts, maximum and mean
accepted scores, family, strength, effective weight and the fusion gates that admitted
the contribution. `strong_maximum` identifies the winning strong branch;
`medium_high_or_independent_family_support` names the actual OR gate, without claiming
which disjunct was necessary when both could hold. `weak_boost_existing_nonweak_support`
requires accepted non-weak support. Non-strong winners also report
`independent_family_maximum`. Actual provisional parameters remain in fusion.score_info.

The statistics explicitly cover positive accepted cells in the original hysteresis
components, including a seed removed by cleanup. They are not detector raw scores,
calibrated confidence or necessarily statistics over the final polygon. This scope
preserves traceability without inventing contribution in newly filled pixels. Missing,
zero-weight, gated-out or family-suppressed contributions do not appear as fired rules.
Raster values, fused-score transport and historical region records are unchanged.

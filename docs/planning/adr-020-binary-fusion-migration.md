# ADR-020: Coordinate binary fusion and measured coverage

## Status

Implementation in progress. Native anomaly adapters and the bounded fused-score
transport have unit tests. Production normalization, fusion, assessment,
segmentation and visualization have **not** migrated to this design yet.

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
Its production activation and downstream consumer migration are still pending.
These checks establish implementation behavior, not validated forensic accuracy.

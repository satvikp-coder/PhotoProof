---
id: PP-066
title: "test(evaluation): build the localization evaluation harness with IoU, F1 and MCC"
milestone: M6
labels: [type:testing, priority:P1, area:testing, effort:medium, status:ready]
depends_on: []
---
## Summary

Implement `tools/evaluate.py`, a reproducible harness that runs PhotoProof profiles over
a dataset and computes pixel-level and image-level metrics, with per-category breakdowns
and confidence intervals.

## Problem Statement

No detector can be evaluated, compared or calibrated without consistent, correct metric
implementations and a reproducible protocol.

## Objective

Correct metric functions (unit-tested against hand-computed values), a runner that
records the manifests used, and Markdown/JSON reports.

## Technical Background

- Pixel level, per image: TP/FP/FN/TN on binary masks;
  `IoU = TP/(TP+FP+FN)`, `F1 = 2TP/(2TP+FP+FN)`,
  `MCC = (TP·TN − FP·FN)/√((TP+FP)(TP+FN)(TN+FP)(TN+FN))`. Edge cases: an empty ground
  truth with an empty prediction → define as TN-only, and report separately.
- Aggregation: mean over images (and pooled), with bootstrap 95% CIs.
- Image level: TPR/FPR from "any region predicted".
- Copy-move masks label source and target separately. Evaluate the union by default and
  report per-part results optionally.

## Functional Requirements

- `evaluate.py run --dataset NAME --profile P [--subset N]` → per-sample results JSONL
  plus a summary report. Each record includes the run ID and computational fingerprint.
- Metric functions in `tools/metrics.py` with explicit edge-case semantics.
- Breakdowns by dataset category (e.g. CoMoFoD post-processing).
- Deterministic sample order and seeds.

## Technical Implementation

`tools/metrics.py`, `tools/evaluate.py`. Uses the PhotoProof controller in-process
(bootstrap once). Results go under `ARE/reports/evaluation/` (Git-ignored); summaries are
copied to docs manually.

## Integration Requirements

The PP-024 and PP-035 mask formats. Dataset loaders from PP-064 and PP-065.

## Security and Edge Cases

Dataset files are untrusted (normal ingestion). Mask resolution differing from the image
(resize the ground truth with nearest neighbour, recorded). Missing outputs (a failed
detector) count as "no prediction" and are reported separately, so they are not hidden
as negatives.

## Testing Requirements

Hand-computed metric cases (perfect, empty, disjoint, partial), MCC denominator-zero
handling, and bootstrap determinism with a seed.

## Acceptance Criteria

- [ ] Metric functions match hand-computed values for all edge cases (tests).
- [ ] Runs record the manifests and fingerprints of every evaluated sample.
- [ ] Failed or missing predictions are reported separately, never silently counted as negatives.
- [ ] Reports include bootstrap CIs and per-category breakdowns.

## Expected Outcome

A trustworthy evaluation foundation.

## Documentation Requirements

`docs/planning/` benchmark strategy, testing page.

## Dependencies

None. Metrics can be built and tested now; dataset runs follow PP-064.

## Priority and Effort

P1, medium.

---
id: PP-037
title: "test(evaluation): evaluate and calibrate fusion thresholds on benchmark data"
milestone: M4
labels: [type:testing, priority:P2, area:testing, area:plugins, effort:large, status:blocked]
depends_on: [PP-033, PP-034, PP-035, PP-066, PP-064]
---
## Summary

Measure the fusion outputs against labelled datasets, select thresholds with a
documented procedure on a held-out split, and publish the resulting operating points.
Until then all scores remain labelled *uncalibrated*.

## Problem Statement

Every fusion and segmentation threshold so far is a provisional starting value. Without
measurement, the system's false-positive behaviour on authentic images is unknown.

## Objective

A reproducible calibration report, and an updated `fusion.*` configuration with
justified thresholds. If calibration succeeds, the `calibration` field switches to
`calibrated:<dataset>@<version>`.

## Technical Background

- Split by *source image* (avoid leakage between forged and authentic versions of the
  same scene), e.g. 60/20/20 train/validation/test.
- Choose thresholds on validation to meet an FPR target on authentic images (for
  example, at most 5% of authentic images with any suspicious region), then report the
  test-set pixel F1, MCC and IoU and the image-level TPR/FPR. The FPR target is a
  maintainer decision recorded in the report.
- Optional score calibration (isotonic regression or Platt scaling) only if the
  validation data support it. Otherwise keep heuristic labels.

## Functional Requirements

- A script `tools/evaluate.py calibrate --dataset … --split …` (built on PP-066) that
  writes a report (Markdown plus JSON) and a proposed config diff.
- Operating-point table: threshold → image-level TPR/FPR, pixel F1/MCC.
- Per-detector ablation: the contribution of each detector family.

## Technical Implementation

Uses the harness from PP-066 and the dataset loaders from PP-064/PP-065. Results are
stored under `docs/planning/results/` (summaries only, no dataset images).

## Integration Requirements

Changes `configs/plugins/fusion.*.yaml` through a normal PR. A changed default threshold
is a behaviour change (MINOR bump) and must cite the report.

## Security and Edge Cases

Dataset licences (no redistribution of images). Class imbalance. Overfitting to one
dataset (report cross-dataset results).

## Testing Requirements

The calibration pipeline runs end to end on a tiny synthetic dataset in CI (smoke test).
Full runs are manual and documented.

## Acceptance Criteria

- [ ] Calibration report published with splits, procedure, operating points and ablations.
- [ ] Thresholds chosen on validation and reported on test, with no test-set tuning.
- [ ] Cross-dataset generalization reported for at least two datasets.
- [ ] The `calibration` label is updated only if justified by the report.

## Expected Outcome

Evidence-based thresholds and a known false-positive rate.

## Documentation Requirements

`docs/planning/` results page and the fusion page thresholds section.

## Dependencies

PP-033, PP-034, PP-035, PP-066, and at least one dataset (PP-064).

## Priority and Effort

P2, large.

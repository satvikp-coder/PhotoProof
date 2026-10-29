---
id: PP-067
title: "test(forensics): benchmark copy-move localization on CoMoFoD and COVERAGE"
milestone: M6
labels: [type:testing, priority:P2, area:testing, area:plugins, effort:medium, status:blocked]
depends_on: [PP-024, PP-064, PP-065, PP-066]
---
## Summary

Run the copy-move chain (PP-020–PP-024) through the evaluation harness on CoMoFoD and
COVERAGE, and publish baseline metrics, per-transformation breakdowns, and an analysis of
the repetitive-structure false positives.

## Problem Statement

The copy-move chain's real-world performance and its failure modes (small regions, flat
textures, repetitive structures, post-processing) are unknown.

## Objective

A published baseline report and a prioritized list of improvement hypotheses. No
accuracy target is set in advance.

## Technical Background

Protocol: use the default configuration from each plugin's documentation. Report
image-level TPR/FPR and pixel F1/IoU/MCC (PP-066). Break down CoMoFoD by transformation
and post-processing. On COVERAGE, report the rate of detections on the similar-but-genuine
objects. Count them as false positives, and also list them separately as "repetitive
structure" cases with the `possible_repetitive_structure` flag (PP-023) agreement rate.

## Functional Requirements

- Reproducible command lines and configuration recorded in the report.
- A parameter sensitivity study for `ratio`, `min_inliers` and `zncc_threshold` on a
  validation subset only.
- Runtime per image recorded (PP-057 metrics).

## Technical Implementation

Uses `tools/evaluate.py`. The report goes to `docs/planning/results/copymove-baseline.md`
(summaries and plots only, no dataset images).

## Integration Requirements

The findings feed PP-037 (thresholds) and PP-071 (dense matching justification).

## Security and Edge Cases

Respect dataset licences in the published figures (no dataset images unless permitted).

## Testing Requirements

The harness smoke test runs on synthetic data in CI. Full runs are manual.

## Acceptance Criteria

- [ ] Baseline metrics with CIs published for both datasets.
- [ ] Per-transformation and per-post-processing breakdowns included.
- [ ] Repetitive-structure analysis included, with flag agreement rates.
- [ ] Every number is reproducible from the recorded commands and fingerprints.

## Expected Outcome

An honest picture of copy-move performance and its failure modes.

## Documentation Requirements

The results page, and limitations added to the copy-move chain docs.

## Dependencies

PP-024, PP-064, PP-065, PP-066.

## Priority and Effort

P2, medium.

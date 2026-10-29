---
id: PP-032
title: "feat(pipeline): normalize detector scores to a common documented scale"
milestone: M4
labels: [type:feature, priority:P1, area:pipeline, area:plugins, effort:medium, status:needs-design]
depends_on: [PP-013, PP-015, PP-019]
---
## Summary

Define the *evidence contract* that every detector emits (an `AnomalyMap` or a region
list with a common normalized score), and implement plugin `fusion.normalize`, which
maps each detector's native scale onto a documented common scale.

## Problem Statement

ELA error levels, noise robust-z scores, DQ probabilities and copy-move correlations live
on incomparable scales. Fusion cannot combine them without a principled common
representation.

## Objective

Each detector's output becomes `EvidenceLayer {detector_id, map in [0,1] (or None),
regions, strength_class, valid_mask, calibration: "uncalibrated"|"calibrated:<dataset>"}`.

## Technical Background

Options: (a) robust z → logistic squashing `s = 1/(1+exp(−(z−z0)/k))` with documented
`z0` and `k` per detector; (b) empirical CDF against authentic-image baselines
(percentile rank), which is more principled once PP-066/PP-068 data exist. Start with
(a) and keep (b) pluggable. Scores remain **uncalibrated heuristics** until PP-037, and
the label must say so.

## Functional Requirements

- A versioned `EvidenceLayer` contract in `photoproof.contracts` (plugin API MINOR
  bump).
- `fusion.normalize` reads all known detector outputs that are *present*. Absent outputs
  produce no layer (never a zero layer).
- Per-detector mapping parameters live in `configs/plugins/fusion.normalize.yaml`, with
  documented defaults and rationale.
- `strength_class` per detector (weak / medium / strong) is fixed in configuration and
  documented (ELA weak; noise medium; DQ medium; verified copy-move strong).

## Technical Implementation

`src/photoproof/plugins/fusion/normalize/`. Inputs are declared as `after`
(ordering-only) on every detector, plus a dynamic read of the outputs that are present.
Since contracts are static, this needs a design decision: either an `optional_inputs`
metadata field (API addition) or explicit `requires` on a fixed detector list. Decide in
the design discussion; status:needs-design.

## Integration Requirements

Plugin ID `fusion.normalize`; priority 90; ordering after all detectors; output
`fusion.layers`. Required by PP-033.

## Security and Edge Cases

A detector that fails → no layer, and an `unavailable` entry in the layers listing. Maps
of different resolutions → resample to a common grid (documented). NaNs → rejected.

## Testing Requirements

The normalization mapping is monotonic. Absent detectors produce no layer (not zeros).
Snapshot of `fusion.layers` for a fixture run.

## Acceptance Criteria

- [ ] `EvidenceLayer` contract defined, documented and versioned.
- [ ] Missing or failed detectors appear as `unavailable`, never as zero-score layers (test).
- [ ] Each detector's mapping parameters and strength class are documented with rationale.
- [ ] All layers carry `calibration: uncalibrated` until PP-037 provides calibration.

## Expected Outcome

A common, honest representation of evidence from heterogeneous detectors.

## Documentation Requirements

A fusion design page (contract, scales, strength classes).

## Dependencies

PP-013, plus at least two detectors (PP-015, PP-019) to validate the design.

## Priority and Effort

P1, medium. The optional-input design needs agreement.

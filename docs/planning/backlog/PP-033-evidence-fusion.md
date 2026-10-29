---
id: PP-033
title: "feat(plugins): fuse evidence with a documented rule-based scheme"
milestone: M4
labels: [type:feature, priority:P1, area:plugins, effort:large, status:blocked]
depends_on: [PP-032]
---
## Summary

Plugin `fusion.rules` combines normalized evidence layers into a fused suspicion map and
per-region evidence summaries, using transparent, documented rules. It is not a learned
or probabilistic model.

## Problem Statement

Individual detectors are noisy and each fails in known situations. Agreement between
independent detectors is far more informative than any single one, but the combination
must be explainable and must never inflate weak cues into strong claims.

## Objective

`fusion.map` (heuristic suspicion score ∈ [0,1], explicitly *not* a probability) plus
`fusion.evidence` (per-region list of contributing detectors and rules fired).

## Technical Background

The rule-based scheme (to be reviewed):

1. Strong evidence (verified copy-move masks) can make a region suspicious alone.
2. Medium evidence (noise inconsistency, DQ) needs support from at least one other
   independent detector family, or a high score.
3. Weak evidence (ELA, sharpness, edges, EXIF) can only *raise confidence* in regions
   already supported by medium or strong evidence. It can never mark a region
   suspicious alone.
4. Detector families (compression / noise / geometry / visual / metadata) count once
   each, so correlated detectors do not double count.

Fused score per pixel: the maximum of the strong layers, combined with a weighted noisy-OR
of the medium and weak layers under the gating rules above. All weights are in
configuration. The output label is `heuristic_score`.

## Functional Requirements

- Rules and weights live in `configs/plugins/fusion.rules.yaml`, validated by schema and
  versioned (a rule change is a plugin MINOR or MAJOR bump).
- Each region in `fusion.evidence` lists the contributing detectors, their scores, the
  rules fired and the families involved.
- ELA alone, or any weak-only combination, never reaches the "suspicious" level (test).
- Missing layers reduce the attainable confidence and are listed.

## Technical Implementation

`src/photoproof/plugins/fusion/rules/`. Pure NumPy. The region extraction is shared with
PP-035 (use its segmentation utilities, or produce coarse regions here and refine there;
decide which in the PR).

## Integration Requirements

Plugin ID `fusion.rules`; priority 90 (after `fusion.normalize`); requires
`fusion.normalize`; outputs `fusion.map`, `fusion.evidence`. Required by PP-034 and
PP-035.

## Security and Edge Cases

All detectors unavailable → no fused map, and the status is passed on to the sufficiency
logic (PP-034). Conflicting evidence (strong copy-move vs nothing else) → reported as is.
Resolution mismatch → common grid.

## Testing Requirements

Table-driven rule tests over synthetic layers (each rule fires, or is gated, as
specified). A property test: adding a weak layer never increases a region's score
unless medium or strong support exists.

## Acceptance Criteria

- [ ] Each rule has positive and negative tests.
- [ ] Weak-only evidence never produces a suspicious region (property test).
- [ ] Every fused region lists its contributing detectors and rules.
- [ ] Output is labelled a heuristic score, and the word "probability" does not appear in outputs or UI strings.

## Expected Outcome

Transparent combined evidence that investigators can audit.

## Documentation Requirements

A fusion design page with every rule, weight, rationale and limitation.

## Dependencies

PP-032.

## Priority and Effort

P1, large.

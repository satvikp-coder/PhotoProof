---
id: PP-071
title: "research(plugins): evaluate dense block-matching copy-move detection for low-texture regions"
milestone: M7
labels: [type:research, priority:P3, area:plugins, effort:large, status:needs-design]
depends_on: [PP-067]
---
## Summary

Investigate a dense block-matching copy-move detector based on Zernike moments, with
PatchMatch-style nearest-neighbour search, for the smooth and low-texture regions where
SIFT finds no keypoints. Implement it only if the PP-067 benchmark shows a gap that
justifies it.

## Problem Statement

Keypoint methods miss copy-moves of flat regions (sky, walls), which are a common way of
hiding objects. Dense methods catch these but are slower and produce more false positives
on repetitive content.

## Objective

A justified go/no-go decision. If go, an optional plugin `copymove.dense` producing the
same mask contract as PP-024.

## Technical Background

Dense copy-move detection (Cozzolino, Poggi & Verdoliva 2015): compute
rotation-invariant features per overlapping block (Zernike moment magnitudes or circular
harmonic transforms), find the nearest neighbours with PatchMatch (randomized
propagation, roughly O(N) per iteration instead of O(N²)), filter the offset field with
a median and dense linear fitting, and post-process. Christlein et al. (2012) benchmark
block-based features.

## Functional Requirements

- Research phase: a prototype on CoMoFoD/COVERAGE subsets via PP-066, with a comparison
  against the keypoint chain on low-texture categories.
- Go criterion: a measured improvement on low-texture cases without an unacceptable FPR
  increase on COVERAGE. The thresholds are chosen and justified in the research report
  before implementation.
- If go: the plugin with the same `copymove.mask` semantics, the `after` relationship
  with the keypoint chain, and fusion family `geometry` (counted once with the keypoint
  chain).

## Technical Implementation

The prototype lives in `tools/research/` first. The production plugin in
`src/photoproof/plugins/copymove/dense/` only after the go decision.

## Integration Requirements

Fusion must treat both copy-move methods as one family (PP-033 rule 4).

## Security and Edge Cases

Runtime and memory on large images (downscale and tile). Repetitive textures (high FP
risk).

## Testing Requirements

Prototype evaluation report. If implemented, unit tests on synthetic flat-region copies
that the keypoint chain misses.

## Acceptance Criteria

- [ ] Research report published with the go/no-go decision and its rationale.
- [ ] If go: the plugin detects synthetic flat-region copies missed by the keypoint chain (test), and COVERAGE FPR is reported.
- [ ] If no-go: the issue is closed with the report linked, and the limitation is documented in the copy-move docs.

## Expected Outcome

An evidence-based answer to the low-texture copy-move gap.

## Documentation Requirements

The research report, and the copy-move chain limitations.

## Dependencies

PP-067.

## Priority and Effort

P3, large. Optional research.

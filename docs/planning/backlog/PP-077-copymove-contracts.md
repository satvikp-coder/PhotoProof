---
id: PP-077
title: "docs(plugins): specify the copy-move chain data contracts"
milestone: M2
labels: [type:documentation, priority:P1, area:plugins, effort:small, status:blocked]
depends_on: [PP-072]
---
## Summary

Define and freeze the interchange formats between the five copy-move stages (keypoints →
matches → clusters → verified transforms → masks) as typed models with serialization
helpers. Then PP-020–PP-024 can be built in parallel by different people.

## Problem Statement

The copy-move chain is strictly sequential at runtime, but it does not have to be
developed sequentially. Without agreed formats, the authors of PP-021–PP-024 must wait for the
previous stage to merge, which serializes five issues.

## Objective

`photoproof.plugins.copymove.contracts` with pydantic or dataclass models and NPZ/JSON
(de)serializers for each stage, plus a synthetic "golden" example of each format
generated from the PP-072 toolkit.

## Technical Background

| Stage | Format |
|---|---|
| Keypoints | `xy float32[N,2]`, `scale float32[N]`, `angle float32[N]`, `response float32[N]`, `desc float32[N,128]` |
| Matches | `pairs int32[M,2]`, `distance float32[M]` |
| Clusters | list of `{id, pair_indices, source_bbox, target_bbox, mean_displacement}` |
| Verified | list of `{cluster_id, matrix float64[2,3], inliers, rmse, scale, rotation_deg, score, flags}` |
| Mask | uint8 PNG with 0 = background, 1 = source, 2 = target, plus a region list |

## Functional Requirements

- Models with validation (shapes, dtypes, index bounds) and round-trip serializers.
- `golden_examples(seed)` builds a consistent example chain from a synthetic copy-move,
  so each stage author can test in isolation against realistic upstream data.
- Contract versioning: each artifact carries `contract_version`.

## Technical Implementation

`src/photoproof/plugins/copymove/contracts.py` (the `copymove` category package, no
`METADATA`) plus tests. Update the copy-move chain docs page.

## Integration Requirements

PP-020–PP-024 must use these models for their outputs and inputs. A change to a contract is a
plugin-version decision documented in the chain page.

## Security and Edge Cases

The validators reject out-of-range indices, NaNs and mismatched array lengths
(defensive, because artifacts are read back from storage).

## Testing Requirements

Round-trip tests for every format. Validation rejects malformed examples. The golden
chain is deterministic.

## Acceptance Criteria

- [ ] All five formats are implemented, validated and round-trip tested.
- [ ] The golden example chain is available as a test fixture.
- [ ] The chain documentation page describes every field.
- [ ] The authors of PP-020–PP-024 have reviewed the PR.

## Expected Outcome

Five copy-move issues can proceed in parallel.

## Documentation Requirements

Copy-move chain page in `docs/plugins/`.

## Dependencies

PP-072 (synthetic copy-move generator for the golden examples).

## Priority and Effort

P1, small.

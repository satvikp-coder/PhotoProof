---
id: PP-027
title: "feat(core): accept and validate target-versus-reference analysis requests"
milestone: M3
labels: [type:feature, priority:P1, area:core, effort:medium, status:blocked]
depends_on: [PP-011, PP-012]
---
## Summary

Complete the comparison mode end to end. The reference goes through the same ingestion
and decoding path as the target. Relationship checks (identical bytes, dimensions,
aspect ratio) run before any comparison plugin, and the reference is consistently
labelled as user-supplied comparison material.

## Problem Statement

The controller already distinguishes `single` and `comparison` profiles and passes
`input.reference` descriptors, but no ingestion, decoding or relationship validation
exists for the reference. It is easy to accidentally treat the reference as a trusted
original.

## Objective

A comparison request is fully validated, and `image.reference_working` is available to
plugins. Manifests and reports state that the reference is **not** assumed authentic.

## Technical Background

The comparison only shows *differences between two user-provided files*. If the
reference is itself manipulated, differences are real but their interpretation inverts.
The terminology must be "target" and "reference", never "original" and "fake".

## Functional Requirements

- The CLI `analyze TARGET --reference REF --profile comparison` ingests both through
  `IngestionService`.
- Relationship pre-check (a run-level step, not a plugin): `identical_bytes`,
  `identical_pixels`, `dimension_ratio`, `aspect_ratio_delta`; recorded in the manifest
  as `comparison.preflight`.
- Identical bytes → the run completes with an informational result "inputs are
  identical"; comparison plugins become `NOT_APPLICABLE`.
- New run contracts: `image.reference_decoded`, `image.reference_working`.
- Size limits apply to the combined memory of both images.

## Technical Implementation

Extend the `ImageResolver` from PP-012 for the reference role. Add `RUN_INPUTS` entries.
The pre-check lives in `core/controller.py` (or a small `core/comparison.py`).

## Integration Requirements

Comparison plugins (PP-028–PP-030) declare `image.reference_working`. The comparison
profile's `required_outputs` will list the change mask once PP-030 exists.

## Security and Edge Cases

A reference in a different format from the target (allowed). Extreme size mismatch (more
than 8× area → warn, still allowed). The same file passed twice. A reference larger than
the limits.

## Testing Requirements

Integration with generated images: identical bytes, re-encoded copy, resized copy,
unrelated image. Assert the manifest wording ("reference: user-supplied comparison
material").

## Acceptance Criteria

- [ ] Both inputs are ingested, stored as evidence and decoded once.
- [ ] Identical inputs produce the documented informational result.
- [ ] Manifest records `comparison.preflight` and the reference trust statement.
- [ ] No output, log or doc string calls the reference "original" or "authentic".

## Expected Outcome

A trustworthy foundation for comparison plugins.

## Documentation Requirements

Configuration (comparison profile), architecture data flow, plugin contracts.

## Dependencies

PP-011, PP-012.

## Priority and Effort

P1, medium.

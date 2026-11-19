# ADR-022: Source-bound copy-move interchange

## Context

The historical typed models and packed runtime NPZ did not share a wire format.
Neither required enough provenance to distinguish an original-frame array from an
EXIF-normalized working-frame array. Feature counts in summaries could not replace
actual artifact reads. A hash validates bytes, not their coordinate meaning.

## Decision

Define version-2 models in `photoproof.contracts.copymove`, with the old plugin
module providing compatibility imports. Require original source SHA-256, working
dimensions, literal working-pixel coordinates and uncalibrated semantics on every
stage. Require parent counts or identifiers for index-bearing records. Reject
nonfinite, out-of-bounds, truncated and wrapping values. Copy validated arrays to
immutable bytes-backed storage.

Use bounded exact-schema numeric NPZ, with UTF-8 JSON encoded as uint8 metadata,
and strict JSON for clusters. Do not use pickled objects or extract archive files.
Use deterministic ZIP metadata for repeatable fixed-platform artifacts. Mask PNG
requires a source-bound JSON sidecar and preserves semantic labels rather than
display colours. The [interchange guide](../plugins/copymove-contracts.md) defines
every field, limit and coordinate convention.

Wire extraction and matching to these exact models through the controller now.
Keep downstream migration explicit: validating transform records does not execute
RANSAC, and candidate matches are insufficient to claim verified localization.
The golden fixture uses actual synthetic generator pixels and labelled ground
truth, with no invented detector or benchmark result.

## Alternatives and consequences

Keeping separate runtime/model formats would perpetuate ambiguous consumers.
Automatically relabelling old artifacts would invent missing provenance. Version 2
therefore breaks artifact compatibility and requires explicit migration or reanalysis.
Historical manifests remain unchanged. Host API 1.7 exposes the new helpers; the
application release version remains unchanged. This PR needs MAJOR release impact.

The technical contract and tests do not substitute for #77's requested review by
the stage authors. That review remains outstanding until actual reviewers provide
it. No independent approval is claimed. Broader integration, performance and
accuracy evaluation remain separately tracked requirements.

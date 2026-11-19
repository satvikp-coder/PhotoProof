# ADR-018: Separate execution facts from missing measurements

## Status
Implemented locally under PP-032/PP-034 migration. API1.6.0; not yet integrated on main.

## Context
A missing optional numerical value does not reveal whether its detector was inapplicable,
failed, skipped, not requested or still pending. Guessing from absence can fabricate clean
evidence or make coverage permanently inconclusive. Detectors should not need core imports,
runtime objects, exception text or filesystem access to distinguish these outcomes.

## Decision
A declaring plugin may request the reserved host input `pipeline.upstream_status`. The
executor binds a fresh version-1 JSON snapshot at invocation, containing planned output
contracts and their producer ID, actual ExecutionState string and optional failure-category
string. The consuming plugin's own outputs are excluded. Future producers remain explicitly
PENDING, never inferred successful. Absent entries mean not in the execution plan.

The capability is provided for required and optional declarations. It does not manufacture
missing output values; failed/inapplicable optional inputs remain absent. Snapshots contain
no exception messages, tracebacks, paths, timestamps, mutable execution objects or code
locations. Existing deep-copy context construction isolates plugin mutation, and a later
producer's state transition does not change earlier snapshots. Plugins cannot produce the
reserved contract. The executor does not decide forensic coverage; that policy remains in
normalization/assessment using measured validity, explicit applicability and configured
requirements. No snapshot state is evidence of image authenticity.

## Compatibility and verification
API1.6.0 is additive; application release version is unchanged. Older contexts need not
provide this capability. Consumers requiring it must declare the host version and contract.
The host API is part of cache identity. The future process executor can transport this small
JSON record without exposing parent runtime objects.

Actual controller fixtures exercise SUCCESS, FAILED, NOT_APPLICABLE and future PENDING
producers, required/optional capability declarations and stable earlier snapshots. Missing
values remain missing and the deliberately failed run remains INCOMPLETE. These are control
plane probes, not detectors or forensic-accuracy data.

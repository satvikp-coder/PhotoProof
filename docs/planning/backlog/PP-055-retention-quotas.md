---
id: PP-055
title: "feat(storage): enforce runtime retention policies and storage quotas"
milestone: M6
labels: [type:feature, priority:P2, area:storage, effort:medium, status:blocked]
depends_on: [PP-001]
---
## Summary

Add configurable retention (age-based) and quotas (size-based) for the runtime data in
`ARE/`, enforced by a housekeeping service. The deletion order prefers disposable data,
and evidence is never deleted without an explicit policy.

## Problem Statement

`ARE/` grows without bound: artifacts, cache entries, manifests, logs and evidence.
Running out of disk space corrupts nothing (writes are atomic) but stops the
application.

## Objective

`retention.*` settings and a `housekeeping` command that reports, then applies,
deletions, with a dry run by default.

## Technical Background

Data classes by disposability: cache entries (disposable) < orphaned artifacts (no
referencing manifest) < old runs' derived artifacts < manifests and DB rows of deleted
runs < evidence (never automatically, unless `retention.evidence_days` is set
explicitly, see PP-056).

## Functional Requirements

- Settings: `retention.cache_days`, `retention.artifact_days`, `retention.run_days`
  (null = keep), `quotas.max_total_mb`, `quotas.warn_at_percent`.
- `photoproof housekeeping [--apply]` (dry run by default) lists what would be deleted
  and why.
- Reference counting: an artifact is deletable only if no retained manifest references it.
- Health check `storage.quota`: WARNING at `warn_at_percent`, ERROR at 100%.
- Every deletion is audited (`HOUSEKEEPING_APPLIED` event).

## Technical Implementation

`core/services/housekeeping.py`. DB queries for run age and references; filesystem
scanning via `RuntimeStorage`. Deletions are of specific files only (no recursive
deletes), consistent with the repair rules.

## Integration Requirements

Optional periodic execution by the service manager (PP-051). Preflight runs the quota
check only (never deletions).

## Security and Edge Cases

Never delete an artifact referenced by a retained manifest. Concurrent analysis during
housekeeping (take a lock, or skip runs younger than N minutes). Clock changes.

## Testing Requirements

Reference-safety test: a referenced artifact is never deleted. Dry-run purity (no
changes). Quota thresholds.

## Acceptance Criteria

- [ ] Dry run is the default and changes nothing (test).
- [ ] Referenced artifacts are never deleted (test).
- [ ] Evidence is untouched unless an explicit evidence retention policy is configured.
- [ ] All deletions are audited.

## Expected Outcome

Bounded, predictable disk usage.

## Documentation Requirements

Settings reference, operations runtime docs.

## Dependencies

PP-001.

## Priority and Effort

P2, medium.

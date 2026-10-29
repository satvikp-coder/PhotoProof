---
id: PP-053
title: "perf(cache): add per-plugin content-addressed result caching"
milestone: M6
labels: [type:enhancement, priority:P3, area:performance, area:pipeline, effort:medium, status:blocked]
depends_on: [PP-012]
---
## Summary

Cache individual plugin results keyed by the plugin identity and its actual inputs, so a
change to one plugin's configuration re-runs only that plugin and its dependents.

## Problem Statement

The run-level cache over-invalidates by design (ADR-008): any settings change re-runs
everything. With expensive detectors (SIFT, DCT models) this slows iteration on fusion
parameters.

## Objective

`PluginCache` keyed by `sha256(plugin_id, version, implementation_sha256, config_digest,
input_contract_digests, relevant distribution versions, decoder versions, seed)`.

## Technical Background

Merkle-style keys: each plugin's input digest includes its upstream outputs' digests, so
invalidation propagates exactly along the DAG. Only `deterministic: true` plugins are
cached. Artifacts are already content-addressed.

## Functional Requirements

- Lookup before invoking a plugin. A hit restores outputs and artifact references (after
  verifying the artifacts) and records `cache: hit` in the plugin's execution record.
- Non-deterministic plugins are never cached.
- `analyze --no-cache` bypasses both caches. A flag `cache.plugin_level` (default false
  until proven).
- Health check: invalid entries are SAFE to invalidate.

## Technical Implementation

`core/cache/plugin_cache.py`; the executor or controller hook in `invoke`. Input digests
are canonical JSON plus the SHA-256 of array contracts (computed once per run).

## Integration Requirements

Manifests record per-plugin cache hits. The run-level cache remains the first check.

## Security and Edge Cases

Key completeness (a missing component → wrong reuse): add a test per component. Disk
growth (respect the quotas in PP-055).

## Testing Requirements

Changing one plugin's config re-runs that plugin and its dependents only (instrumented
counters). Non-deterministic plugins always run. Corrupted entry → recomputation.

## Acceptance Criteria

- [ ] A config change for one plugin re-executes exactly that plugin and its transitive dependents (test).
- [ ] Each key component has a test proving that it invalidates.
- [ ] Non-deterministic plugins are never served from cache.
- [ ] A speedup is measured and documented on a representative run.

## Expected Outcome

Faster iteration without correctness risk.

## Documentation Requirements

Operations runtime docs (cache section), ADR-008 follow-up.

## Dependencies

PP-012 (array contracts to digest).

## Priority and Effort

P3, medium. An optimization; do it only when profiling shows the need.

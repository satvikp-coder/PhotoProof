---
id: PP-084
title: "feat(contracts): provide bounded verified reads of declared artifact inputs"
milestone: M6
labels: [type:feature, priority:P1, area:pipeline, area:plugins, effort:medium, status:in-progress]
depends_on: [PP-012]
integrates_with: [PP-027, PP-052]
---
## Summary

Provide a narrow PluginContext operation that reads bytes from a declared upstream
artifact input after validating its current-run provenance, size and content hash.

## Problem Statement

Detectors persist arrays as content-addressed NPY/NPZ/PNG artifacts, but downstream
plugins currently receive only references. Treating references as numerical arrays
cannot implement actual detector chains; granting filesystem paths would bypass the
framework evidence boundary. Existing PP-012 pixel access does not supply artifact reads.

## Objective

A plugin can obtain verified bytes for a declared artifact input without arbitrary blob,
path, URL or cross-run access. This is a prerequisite for real detector and comparison
integration, not a claim of process isolation.

## Technical Background

The controller already brokers artifact writes. It can likewise broker reads using
registered ArtifactRef records, the execution plan and RuntimeStorage, with verification
before bytes are returned. The future process executor can expose the same operation
through a bounded parent/child protocol.

## Functional Requirements

- Add a typed optional ArtifactReader capability and PluginContext.artifact operation;
  retain existing context construction compatibility and bump plugin API MINOR.
- Accept only names in the plugin's resolved declared inputs, with an optional bounded
  JSON-key/index selector for structured artifact collections, resolving a canonical
  artifact reference with provenance from that contract's successful producer.
- Require exact metadata equality with an artifact written in the current run; no
  arbitrary hash lookup, URL resolution, file handle or filesystem path capability.
- Check declared size before opening, enforce `resources.max_artifact_read_bytes`
  independently of raw-image limits while reading,
  and verify actual size and SHA-256 before returning immutable bytes.
- Preserve missing or corrupt files and fail visibly; never repair evidence/artifacts.

## Technical Implementation

Stable callable/type and convenience method in contracts/plugins.py; controller-owned
reader implementation using verified artifact records and safe RuntimeStorage paths.
Array parsing remains the consuming plugin's responsibility, with allow_pickle=False
and appropriate shape/resource validation for numerical formats.

## Integration Requirements

Use the real controller with a producer writing an array artifact and a consumer reading
its declared input. Coordinate API version allocation with PP-027; adapt the capability
to process isolation in PP-052. Existing contexts/plugins that never read artifacts retain
behavior. Updating individual detector algorithms remains in their existing issues.

## Security and Edge Cases

Deny undeclared names, forged references, producer mismatches, cross-run references,
symlinks, oversized/truncated/altered blobs and invalid requested ceilings. Only successful
upstream outputs authorize reads. The capability does not isolate trusted Python code,
bound total plugin RSS, or authorize later web artifact endpoints.

## Testing Requirements

Unit capability checks plus controller integration with a genuine numerical artifact.
Security regressions for provenance, tampering, size limits before allocation and missing
files, all in temporary runtime roots. Verify the original input is unchanged.

## Acceptance Criteria

- [ ] A real producer-consumer controller run reads the exact verified artifact bytes.
- [ ] Undeclared/forged/cross-run references are rejected without arbitrary file access.
- [ ] Declared and actual read limits are enforced and hashes verified before return.
- [ ] Corrupt or absent artifacts fail visibly and are preserved unchanged.
- [ ] Existing plugin contexts remain compatible and the API addition is documented.

## Expected Outcome

Actual upstream evidence can be consumed safely by downstream plugins instead of fake
arrays or undocumented filesystem access.

## Documentation Requirements

An ADR for the read capability, contract documentation, plugin examples and limitations.

## Dependencies

PP-012. Integrates with PP-027 and PP-052.

## Priority and Effort

P1, medium. Owner: Satvik Patel (owner:satvik on GitHub).

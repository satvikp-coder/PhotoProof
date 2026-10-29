---
id: PP-048
title: "feat(cli): visualize and compare execution manifests"
milestone: M5
labels: [type:feature, priority:P3, area:core, effort:small, status:ready]
depends_on: []
---
## Summary

Extend the `manifest` CLI (and later WebKit) with a dependency-graph rendering (Mermaid
or DOT), a stage timeline, and `manifest diff RUN_A RUN_B`, which explains why two runs
differ (settings, flags, plugin versions, digests, states, outputs).

## Problem Statement

Manifests are long JSON documents. Comparing two runs ("why did the result change?") is
currently manual.

## Objective

Human-readable manifest views and a structured diff aligned with the cache-key
components.

## Technical Background

A JSON structural diff with a domain-aware ordering: identity → inputs → configuration →
plugins → graph → states → outputs. Fingerprint equality short-circuits the diff
("computationally identical").

## Functional Requirements

- `manifest RUN_ID --graph mermaid|dot` prints the dependency graph with state colours
  in labels.
- `manifest RUN_ID --timeline` prints a per-plugin start and duration table.
- `manifest diff A B [--json]` lists the differences grouped by category, and states
  whether the computational fingerprints match.

## Technical Implementation

`src/photoproof/core/pipeline/manifest_tools.py` (pure functions); CLI wiring in
`cli.py`.

## Integration Requirements

WebKit can later render the Mermaid output (PP-049 diagnostics page).

## Security and Edge Cases

Run IDs pass through `safe_join` (already the case). Manifests from older
`manifest_version` values → explicit "unsupported version" error.

## Testing Requirements

Diff tests on synthetic manifests (one change per category), graph output snapshot, and
the identical-fingerprint short-circuit.

## Acceptance Criteria

- [ ] `manifest diff` reports every changed category on synthetic manifests (tests).
- [ ] The graph output renders in MkDocs Mermaid (validated in a docs example).
- [ ] Identical computational fingerprints are reported as such.

## Expected Outcome

Fast answers to "what changed between these runs?"

## Documentation Requirements

Operations runtime docs (manifest tooling).

## Dependencies

None. It works on today's manifests.

## Priority and Effort

P3, small. A good first issue for a new contributor.

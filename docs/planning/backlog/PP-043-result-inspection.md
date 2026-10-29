---
id: PP-043
title: "feat(WebKit): show detailed per-detector forensic results"
milestone: M5
labels: [type:feature, priority:P2, area:WebKit, effort:medium, status:blocked]
depends_on: [PP-042, PP-034]
---
## Summary

A details panel on the result page: the assessment category with reasons, the coverage
table, and per-detector cards (status, key numbers, maps, limitations, configuration
used), each linked to the manifest.

## Problem Statement

A heatmap without its reasoning invites over-interpretation. Investigators need to see
why something was flagged and what was not checked.

## Objective

A structured, readable breakdown that mirrors `fusion.assessment`, `fusion.evidence` and
the per-plugin outputs.

## Technical Background

Progressive disclosure: summary first, details on expansion. Every detector card includes
its fixed "limitations" text from the detector documentation.

## Functional Requirements

- Assessment banner: the category and reasons (PP-034 templates).
- Coverage table by detector family.
- Detector cards: state, a summary scalar, a map thumbnail (opens in the viewer), config
  values, plugin version, and limitations.
- A region list: clicking a region shows its contributing detectors and fired rules.

## Technical Implementation

`WebKit/pages/result.*`, using manifest data. Detector card content is supplied by a
small per-plugin `describe()` contract (rendering hints only; no algorithm code in
WebKit). Decide whether it is metadata or a function.

## Integration Requirements

Reads manifests and artifacts. Requires stable output schemas from the detector issues.

## Security and Edge Cases

Escape all values. Large region lists (paginate). Missing outputs → "unavailable" with
the reason.

## Testing Requirements

Rendering tests for each assessment category, and for failed, skipped and not-applicable
cards.

## Acceptance Criteria

- [ ] Assessment, coverage and per-detector details render from manifest data.
- [ ] Each detector card shows its limitations text.
- [ ] Unavailable evidence is explicit for every failed, skipped or not-applicable detector.

## Expected Outcome

Results are explainable in the UI.

## Documentation Requirements

The user guide (interpreting results).

## Dependencies

PP-042, PP-034.

## Priority and Effort

P2, medium.

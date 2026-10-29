---
id: PP-034
title: "feat(plugins): determine evidence sufficiency and report inconclusive results"
milestone: M4
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-033]
---
## Summary

Plugin `fusion.assessment` produces the run's overall *assessment category*. It takes
into account which detectors were available and applicable, so that missing evidence
leads to **inconclusive** rather than an implied clean result.

## Problem Statement

The most dangerous failure for a forensic tool is "nothing found" being read as
"authentic" when detectors did not run, were not applicable (e.g. ELA on a PNG
screenshot), or had low confidence.

## Objective

One of: `manipulation_indicated`, `differences_found` (comparison mode),
`no_indicators_found_with_sufficient_coverage`, or `inconclusive`. Each comes with the
reasons and a coverage table.

## Technical Background

Coverage matrix: detector families × applicability × confidence. Define *sufficient
coverage* as at least N independent families applicable with non-low confidence
(starting value N = 2, documented as provisional until PP-037). There is no
"authentic" category at all.

## Functional Requirements

- Output `fusion.assessment`: `{category, reasons[], coverage{family: state}, limitations[]}`.
- Any required detector unavailable, or coverage below the threshold → `inconclusive`.
- The text templates for every category are reviewed and fixed. The category name
  never contains "authentic", "genuine" or "real".
- The `RunStatus` (pipeline health) and the assessment category are distinct concepts.
  Both are shown.

## Technical Implementation

`src/photoproof/plugins/fusion/assessment/`; coverage rules in config.

## Integration Requirements

Plugin ID `fusion.assessment`; priority 95; requires `fusion.rules`; `after` all
detectors; output `fusion.assessment`. Consumed by reports (PP-046) and WebKit.

## Security and Edge Cases

All detectors failed → `inconclusive` with reasons. Comparison and single-image evidence
mixed → both categories may apply; define the precedence.

## Testing Requirements

Truth-table tests over coverage states. A forbidden-word test on templates. An
integration test where every detector is `NOT_APPLICABLE` → `inconclusive`.

## Acceptance Criteria

- [ ] Missing, failed or not-applicable detectors can only lead to `inconclusive`, never to a clean category (tests).
- [ ] No category, template or UI string uses "authentic", "genuine" or "real".
- [ ] The coverage table is included in every assessment.

## Expected Outcome

Honest overall results that make the limits of the evidence explicit.

## Documentation Requirements

A fusion page section on assessment categories, and the reporting docs.

## Dependencies

PP-033.

## Priority and Effort

P1, medium.

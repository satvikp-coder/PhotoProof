---
id: PP-031
title: "feat(reporting): interpret comparison evidence in reports"
milestone: M3
labels: [type:feature, priority:P2, area:core, effort:medium, status:blocked]
depends_on: [PP-046]
integrates_with: [PP-030]
---
## Summary

Add a comparison section to structured reports. It explains registration quality, lists
change regions with thumbnails, and states clearly what a difference does and does not
mean.

## Problem Statement

A difference between two files shows that they differ, not which one is authentic.
Without careful wording, reports could mislead readers.

## Objective

A report section with fixed, reviewed interpretation text and traceable references to
the comparison artifacts.

## Technical Background

Evidence categories (shared with PP-046): **confirmed difference** (measured between two
inputs), **suspected manipulation** (a single-image detector signal), and **unavailable
evidence** (a plugin failed, was skipped or was not applicable).

## Functional Requirements

- Section content: input identities (hashes), the reference trust statement,
  registration model and quality, overlap fraction, global SSIM, a change-region table
  (id, bbox, area %, mean dissimilarity) with preview crops (PP-010), and limitations.
- Interpretation text templates selected by condition (registration failed, no
  differences, many differences, localized differences). Templates live in versioned
  files.
- Every number links to its manifest field or artifact hash.

## Technical Implementation

A report-section builder in the reporting module (PP-046). Jinja2 (or the template
system chosen in PP-046). Crops are taken from previews, never from the originals.

## Integration Requirements

Consumes the outputs of PP-028–PP-030. Rendered in the WebKit report preview (PP-045)
and the PDF (PP-047).

## Security and Edge Cases

Never embed the originals. Escape all text derived from inputs (file names). Missing
plugin outputs → the section says "unavailable", never "no differences".

## Testing Requirements

Snapshot tests of the section model for each interpretation condition. Assert the
absence of the forbidden words ("original", "authentic", "fake") in the generated text.

## Acceptance Criteria

- [ ] Each interpretation condition has a template and a snapshot test.
- [ ] Every numeric value is traceable to a manifest field or an artifact hash.
- [ ] Missing comparison outputs are rendered as "unavailable evidence".
- [ ] Forbidden-word test passes.

## Expected Outcome

Comparison results are communicated accurately.

## Documentation Requirements

The report format documentation, and the comparison chain page.

## Dependencies

**Blocked by** (merge these before starting): PP-046.

**Integrates with** (start now; finish the integration once these land): PP-030.

**Work plan.** Start now against the documented contracts of the integrating issues (use mocks or synthetic fixtures). Complete the integration acceptance criteria when they land.

## Priority and Effort

P2, medium.

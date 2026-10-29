---
id: PP-076
title: "docs(planning): add a contributor work-allocation guide for parallel development"
milestone: M6
labels: [type:documentation, priority:P2, area:documentation, effort:small, status:ready]
depends_on: []
---
## Summary

Document how the team picks, claims and parallelizes backlog issues: hard dependencies
versus integration gates, the two-PR detector workflow, how to claim an issue, WIP
limits, and how to avoid merge conflicts in shared files.

## Problem Statement

The backlog now separates "blocked by" (cannot start) from "integrates with" (start now,
wire up later), and detectors follow an algorithm-first, two-PR workflow. Without a short
written guide, people will either wait unnecessarily or build incompatible pieces.

## Objective

`docs/development/workflow.md`, linked from CONTRIBUTING.md and the roadmap.

## Technical Background

Kanban basics adapted to the repository: one `status:in-progress` issue per person
(at most two), claim by assigning yourself and commenting, and unassign if inactive for
seven days. Shared files with frequent conflicts: `CHANGELOG.md` (add entries under the
right heading; rebase before merge), `app.toml` version (the last PR to merge rebases
and re-bumps), and `mkdocs.yml` nav.

## Functional Requirements

- Sections:
  - choosing an issue (`status:ready`, the critical path, your area);
  - claiming it;
  - hard versus soft dependencies;
  - the two-PR detector plan (`algorithm.py` with synthetic tests, then `processor.py`
    wiring after PP-012);
  - handling version and changelog conflicts;
  - when to split an issue;
  - weekly sync checklist.
- A worked example: taking PP-015 (ELA) from claim to the second merged PR.

## Technical Implementation

Markdown page plus nav entry. Optionally a GitHub Project board with Status, Area and
Milestone views (document its URL if created).

## Integration Requirements

CONTRIBUTING.md §5 links to it.

## Security and Edge Cases

Not applicable (documentation only).

## Testing Requirements

`mkdocs build --strict` passes.

## Acceptance Criteria

- [ ] `docs/development/workflow.md` covers every listed section and includes the worked example.
- [ ] CONTRIBUTING.md and the roadmap link to it.
- [ ] The docs build passes in strict mode.

## Expected Outcome

The team works in parallel with few conflicts and no idle waiting.

## Documentation Requirements

This issue is documentation.

## Dependencies

None.

## Priority and Effort

P2, small.

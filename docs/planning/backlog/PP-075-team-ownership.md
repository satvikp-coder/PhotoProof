---
id: PP-075
title: "chore(repo): record team members and area ownership"
milestone: M6
labels: [type:maintenance, priority:P1, area:CI, area:documentation, effort:small, status:ready]
depends_on: []
---
## Summary

Replace the placeholder developers in `app.toml`, assign area owners in `CODEOWNERS`,
and record who owns which area of the backlog, so reviews are routed automatically and
the work is visibly shared.

## Problem Statement

Three of the four `[[developers]]` entries are placeholders, and `CODEOWNERS` assigns
everything to two accounts. In a group project, unclear ownership causes idle team
members, duplicated work and review bottlenecks.

## Objective

Accurate identity and ownership data, and an agreed area split that matches the
milestones.

## Technical Background

GitHub requests reviews from the matching `CODEOWNERS` entry (the last match wins).
Suggested area split for four people (adjust at kickoff):

| Owner | Areas | Starting issues |
|---|---|---|
| A | ingestion, imaging, security isolation | PP-001, PP-002, PP-003, PP-073 |
| B | compression and noise detectors | PP-072, PP-013, PP-015, PP-018 |
| C | copy-move chain and comparison | PP-077, PP-020, PP-021, PP-028 |
| D | WebKit, reporting, evaluation | PP-038, PP-046, PP-066, PP-048 |

## Functional Requirements

- `app.toml` lists the four real names and GitHub handles.
- `CODEOWNERS` has per-path owners for `src/photoproof/core/`, `src/photoproof/imaging/`,
  `src/photoproof/plugins/<family>/`, `WebKit/`, `docs/`, `.github/`, with a secondary
  reviewer for each.
- `docs/planning/team.md` records the area split and the review rotation (linked from
  the roadmap).

## Technical Implementation

Edit `app.toml`, `.github/CODEOWNERS`, and add the docs page. Add the page to the nav.

## Integration Requirements

A PATCH version bump and a changelog entry, as for any PR.

## Security and Edge Cases

Only handles of people who consent. No personal email addresses in the repository.

## Testing Requirements

`tests/unit/test_config.py::test_repository_configuration_is_valid` keeps passing
(`app.toml` still validates).

## Acceptance Criteria

- [ ] No placeholder developers remain in `app.toml`.
- [ ] `CODEOWNERS` routes every top-level area to a primary and a secondary reviewer.
- [ ] `docs/planning/team.md` is published, and each team member has at least one assigned starting issue.

## Expected Outcome

Clear ownership, and review requests that go to the right people automatically.

## Documentation Requirements

`docs/planning/team.md`, roadmap link.

## Dependencies

None. Do it at the team kickoff.

## Priority and Effort

P1, small.

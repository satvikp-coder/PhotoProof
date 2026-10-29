---
id: PP-063
title: "chore(repo): configure branch protection, merge settings and Read the Docs"
milestone: M6
labels: [type:maintenance, priority:P1, area:CI, area:documentation, effort:small, status:ready]
depends_on: []
---
## Summary

A repository admin applies the branch ruleset and merge settings documented in
`docs/development/ci.md`, and connects Read the Docs. These steps need admin rights that
the foundation setup did not have.

## Problem Statement

The issue-driven workflow (CONTRIBUTING.md §5) is only enforced if `main` is protected.
Currently anyone with write access can push directly, and the documentation is not
published.

## Objective

`main` requires PRs, an approval, passing checks and resolved conversations. Only squash
merges are allowed. The docs build on Read the Docs for `main` and for PRs.

## Technical Background

GitHub rulesets (Settings → Rules). Required status-check names must match the job names
in the workflows exactly. Read the Docs reads `.readthedocs.yaml`.

## Functional Requirements

- Ruleset for `main`: block force pushes and deletions; require a PR with 1 approval,
  dismiss stale approvals, require conversation resolution; required checks
  `Quality · lint, format, types, config`, `Summary`, `Package build`,
  `MkDocs strict build`, `Title · issue · version · changelog`,
  `Dependency audit and lockfile integrity`, `CodeQL`.
- Merge settings: squash only, with "use PR title" as the default commit message.
- Enable private vulnerability reporting (Security → Settings), as referenced in
  SECURITY.md.
- Import the project in Read the Docs, enable PR builds, and add the RTD badge to the
  README.

## Technical Implementation

Repository settings (admin). The README badge change goes through a normal PR (PATCH
bump).

## Integration Requirements

CONTRIBUTING.md and `docs/development/ci.md` must match the final configuration.

## Security and Edge Cases

Keep the bypass list to admins only. Verify that Dependabot PRs can still pass
governance (or document the label exemption).

## Testing Requirements

A test PR: a direct push to `main` is rejected; a PR without approval cannot merge; a PR
with a failing check cannot merge.

## Acceptance Criteria

- [ ] A direct push to `main` is rejected (verified).
- [ ] Merging requires approval, passing checks and resolved conversations (verified with a test PR).
- [ ] Private vulnerability reporting is enabled.
- [ ] Read the Docs builds `main` successfully, and the badge is added to the README.

## Expected Outcome

The documented workflow is enforced, and the docs are public.

## Documentation Requirements

Update `docs/development/ci.md` to note that the settings are applied, and record the
date.

## Dependencies

None.

## Priority and Effort

P1, small. It requires an admin.

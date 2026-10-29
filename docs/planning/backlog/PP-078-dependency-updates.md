---
id: PP-078
title: "ci(deps): regenerate lockfiles automatically for dependency updates"
milestone: M6
labels: [type:maintenance, priority:P1, area:CI, area:security, effort:medium, status:ready]
depends_on: []
---
## Summary

Add a scheduled workflow that regenerates the three hash-pinned lockfiles with `uv`,
runs the test suite, and opens a PR that already contains the PATCH version bump and
changelog entry. Configure Dependabot so its PRs no longer fail governance.

## Problem Statement

Found in the code review:

- Dependabot's `pip` ecosystem updates `pyproject.toml` constraints but cannot
  regenerate the universal, hash-pinned `requirements*.lock` files. Security fixes in
  dependencies therefore never reach the installed environment automatically.
- Dependabot PRs cannot bump `app.toml` or add a changelog entry, so the PR-governance
  workflow fails every one of them. The only escape is the manual `governance:exempt`
  label.

## Objective

Dependency security updates flow into the lockfiles weekly, through PRs that pass every
check without manual edits.

## Technical Background

`uv pip compile --upgrade` (or `--upgrade-package X`) produces the new lockfiles. A
workflow with `contents: write` and `pull-requests: write` can push a branch and open a
PR. Tokens created by `GITHUB_TOKEN` do not trigger other workflows, so CI must be
triggered with a GitHub App token or `workflow_dispatch`.

## Functional Requirements

- Weekly plus manual workflow `deps.yml`. It regenerates all three lockfiles with the
  commands in CONTRIBUTING.md §1.8 (pinned `uv` version, verified checksum), bumps
  PATCH, and adds a `### Security`/`### Changed` changelog entry listing the changed
  packages.
- Skips when nothing changed. One open PR at a time.
- The PR body references a standing tracking issue, so the governance issue check passes.
- Dependabot: keep `github-actions`. Replace the `pip` ecosystem with this workflow, or
  restrict it to security alerts.
- pip-audit findings on `main` trigger the workflow.

## Technical Implementation

`.github/workflows/deps.yml`, plus a small `governance.py deps-pr` helper that writes the
version and changelog edits. Document the token choice.

## Integration Requirements

Uses the existing governance and security workflows. CONTRIBUTING.md §1.8 is updated.

## Security and Edge Cases

Pin and verify the `uv` binary. Never auto-merge. A failing test suite leaves the PR
open in draft, labelled. Least-privilege permissions, and no `pull_request_target`.

## Testing Requirements

`governance.py deps-pr` unit tests (version bump, changelog insertion). A manual dry-run
of the workflow on a fork.

## Acceptance Criteria

- [ ] A weekly run produces a PR with regenerated lockfiles, a PATCH bump and a changelog entry.
- [ ] That PR passes PR governance without the exemption label.
- [ ] No duplicate PRs are opened while one is pending.
- [ ] Documentation explains the dependency update process.

## Expected Outcome

Dependency fixes reach users without manual lockfile work.

## Documentation Requirements

CONTRIBUTING.md §1.8, `docs/development/ci.md`.

## Dependencies

None.

## Priority and Effort

P1, medium. Today, dependency security fixes depend on someone remembering to relock.

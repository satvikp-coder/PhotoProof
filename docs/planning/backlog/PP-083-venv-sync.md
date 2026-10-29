---
id: PP-083
title: "feat(launcher): synchronize the virtual environment exactly with the lockfiles on repair"
milestone: M6
labels: [type:enhancement, priority:P3, area:core, effort:small, status:ready]
depends_on: []
---
## Summary

Make `python main.py --repair` remove packages that no lockfile pins, so `.venv`
exactly matches the locked dependency set.

## Problem Statement

Found in the code review: `pip install -r requirements.lock` adds and upgrades packages
but never removes them. Dependencies dropped from the lockfiles (PyYAML, for example,
after the switch to ruamel.yaml) stay installed and importable, including any with
known vulnerabilities. `--doctor` now *reports* these unlocked packages; nothing removes
them.

## Objective

A CONTROLLED, explicit synchronization during `--repair`, with dry-run output.

## Technical Background

The union of names pinned in `requirements*.lock` is the allowed set, plus `pip`,
`setuptools`, `wheel` and `photoproof`. Development tools from the dev and tools
lockfiles are part of the union, so contributor environments are not stripped.

## Functional Requirements

- `--repair` uninstalls unlocked distributions with `pip uninstall -y` (argument list)
  after printing them.
- `--repair --dry-run` lists what would be removed.
- A normal launch never removes anything.

## Technical Implementation

`main.py`, reusing `unlocked_packages`.

## Integration Requirements

Documented in the operations launcher page and CONTRIBUTING.md.

## Security and Edge Cases

Never uninstall the running pip or the project itself. Distributions without metadata
names are skipped with a warning.

## Testing Requirements

Unit tests with mocked subprocess calls: dry-run lists only; repair calls uninstall
with exactly the extras.

## Acceptance Criteria

- [ ] `--repair` removes exactly the unlocked packages (test).
- [ ] `--repair --dry-run` lists them without changes (test).
- [ ] A normal launch never uninstalls anything (test).

## Expected Outcome

An environment that cannot silently drift from the reviewed lockfiles.

## Documentation Requirements

Operations launcher page.

## Dependencies

None.

## Priority and Effort

P3, small.

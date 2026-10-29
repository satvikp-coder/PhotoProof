---
id: PP-074
title: "feat(cli): scaffold new plugins with a generator command"
milestone: M2
labels: [type:feature, priority:P2, area:plugins, area:core, effort:small, status:ready]
depends_on: []
---
## Summary

`photoproof new-plugin <family>.<name>` generates a correct plugin skeleton
(`__init__.py` metadata, `algorithm.py`, `processor.py`, `schema.py`, a docs page stub
and a test file), so four developers produce consistently structured detectors.

## Problem Statement

Each detector issue requires the same boilerplate: metadata, schema, processor, tests and
a docs page. Hand-copying it leads to inconsistent metadata, missing tests, and
algorithms mixed into the plugin wiring.

## Objective

A generator whose output passes registry validation, lint, mypy and its own generated
test immediately.

## Technical Background

The structure enforces the two-PR work plan used across the backlog: pure algorithm code
in `algorithm.py` (NumPy in, results out, no `PluginContext`), and thin wiring in
`processor.py` (context → algorithm → outputs).

## Functional Requirements

- Arguments: plugin ID, `--priority`, `--requires`, `--after`, `--inputs`, `--outputs`
  (defaults derived from the family).
- Validates the ID format and refuses to overwrite existing directories.
- Generated files:
  - `__init__.py` with complete `METADATA`;
  - `algorithm.py` with a typed stub that raises `NotImplementedError`;
  - `processor.py` wiring;
  - `schema.py` (pydantic config with an example field);
  - `tests/unit/plugins/test_<family>_<name>.py` (metadata validity plus an algorithm test
    marked `xfail` until implemented);
  - `docs/plugins/<family>-<name>.md` with the detector page template (mathematical
    basis, parameters, limitations, false positives, applicable formats, references).
- Prints the next steps (add the docs page to the nav, write the algorithm, bump the
  version, update the changelog).

## Technical Implementation

Templates as package data under `src/photoproof/core/scaffold/templates/`; uses
`string.Template` (no template-engine dependency). CLI wiring in `cli.py`.

## Integration Requirements

Detector issues should use it. Update `docs/plugins/index.md` to recommend it.

## Security and Edge Cases

Paths are built with `safe_join` under `src/photoproof/plugins/`. IDs are validated by
the same regex as `PluginMetadata`.

## Testing Requirements

Generate a plugin into a temporary package root, then run registry discovery and check
that the metadata validates, ruff passes on the generated files, and the generated test
runs.

## Acceptance Criteria

- [ ] Generated plugins are discovered and validated by the registry without edits (test).
- [ ] Generated code passes ruff and mypy (test).
- [ ] The command refuses invalid IDs and existing directories.
- [ ] `docs/plugins/index.md` documents the command and the algorithm/processor split.

## Expected Outcome

Consistent detector packages from every team member.

## Documentation Requirements

Plugin development guide.

## Dependencies

None.

## Priority and Effort

P2, small. A good first issue.

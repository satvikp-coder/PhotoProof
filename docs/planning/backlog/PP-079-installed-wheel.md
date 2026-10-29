---
id: PP-079
title: "feat(core): support running PhotoProof from an installed wheel"
milestone: M6
labels: [type:feature, priority:P2, area:core, effort:medium, status:needs-design]
depends_on: []
---
## Summary

Decide on, then implement, a way to run PhotoProof when it is installed as a wheel
rather than from a checkout: packaged default configuration, an explicit runtime root,
and identity validation that does not require the `src/` layout.

## Problem Statement

Found in the code review: `bootstrap` resolves the repository root as three
directories above its own file and requires the package to live in `src/photoproof/`.
The release workflow attaches a wheel, but an installed wheel fails at startup with an
identity error. The release currently implies an installable product it cannot be.
(The release documentation now states that wheels are verification artifacts only.)

## Objective

Either a supported installed mode, or an explicit decision to stop publishing wheels.
The decision is recorded in an ADR.

## Technical Background

Options:

1. Ship `app.toml`, `settings.yaml` and the profiles as package data, read with
   `importlib.resources`. The runtime root comes from `--root` or
   `PHOTOPROOF_HOME` (default: a platform data directory, e.g. via `platformdirs`).
2. Keep checkout-only, and attach only the sdist to releases.

The version source must remain `app.toml` (ADR-002).

## Functional Requirements

- If option 1: a CLI `--root PATH` and an allowlisted `PHOTOPROOF_HOME` override;
  identity validation that accepts site-packages installs; and `init` to write editable
  copies of the default configuration.
- If option 2: the release workflow stops attaching the wheel, and the docs say so.

## Technical Implementation

`core/bootstrap.py` root resolution, `core/config/loader.py` package-data fallback, the
hatch build config to include the config files, `cli.py` options.

## Integration Requirements

The launcher keeps the checkout behaviour. The release workflow and docs are updated.

## Security and Edge Cases

`PHOTOPROOF_HOME` must go through the existing environment-override allowlist. The
runtime root must not default to a world-writable location.

## Testing Requirements

CI job: build the wheel, install it into a fresh venv outside the checkout, run
`photoproof --root <tmp> status` and an analysis.

## Acceptance Criteria

- [ ] ADR recorded with the chosen option.
- [ ] The chosen option is implemented, and the CI install-from-wheel test passes (option 1), or wheels are no longer attached to releases (option 2).

## Expected Outcome

Releases match what users can actually run.

## Documentation Requirements

ADR, `docs/development/releases.md`, operations docs.

## Dependencies

None.

## Priority and Effort

P2, medium.

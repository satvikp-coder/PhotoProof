---
id: PP-012
title: "feat(pipeline): expose decoded working images to plugins through the execution context"
milestone: M1
labels: [type:feature, priority:P1, area:pipeline, area:plugins, effort:medium, status:blocked]
depends_on: [PP-004, PP-009, PP-011]
---
## Summary

Extend plugin API 1.0 → **1.1** so plugins can read decoded pixels and the original
bytes through `PluginContext`, without filesystem access, and enforce
`supported_formats` applicability.

## Problem Statement

Plugins currently receive only `InputDescriptor` JSON. No real detector can be written
until pixels are available through a stable, read-only contract.

## Objective

New run-level contracts `image.decoded`, `image.working` (and `image.working_hdr` when
available) and `image.original_bytes`, resolved lazily, cached per run, and handed to
plugins as read-only NumPy arrays.

## Technical Background

Decoding is expensive and should happen once per run, not once per plugin. NumPy arrays
can be made read-only (`arr.flags.writeable = False`), which prevents accidental
mutation shared between plugins. Lazy resolution means a run whose plugins need only
metadata never decodes pixels.

## Functional Requirements

- `ctx.image(name) -> numpy.ndarray` (read-only, shape `(H, W, C)`, documented dtype),
  available only for contracts declared in `inputs`.
- `ctx.original_bytes() -> bytes`, available only if `input.original_bytes` is declared
  (used by EXIF/DCT plugins).
- Decode happens once per run, through the isolated decoder (PP-009), on first request.
- Decode failure marks dependent plugins `SKIPPED` (`dependency_unavailable`) and records
  the decode error in the manifest. It must never be interpreted as clean evidence.
- `supported_formats` enforcement (from PP-002) moves into the executor's applicability
  check.
- Host `plugin_api_version` becomes `1.1.0`. Plugins built for 1.0.0 remain compatible.

## Technical Implementation

- Add `numpy` (hash-pinned; regenerate the lockfiles).
- `contracts/plugins.py`: extend `PluginContext` with `image` and `original_bytes`
  callables (still a frozen dataclass; no paths).
- `core/controller.py`: a per-run `ImageResolver` with memoization.
- Update `RUN_INPUTS` in contracts and the planner's input validation.

## Integration Requirements

All detector issues (M2–M4) depend on this. Update the mock plugins to exercise the new
API. Add the decode library versions and normalization policy to the cache key.

## Security and Edge Cases

Read-only arrays (a mutation attempt raises `ValueError`; add a test). The memory
footprint is checked against limits. Undeclared contract access raises
`PolicyError`.

## Testing Requirements

Unit: resolver decodes once for several plugins; undeclared access is denied; arrays are
read-only. Integration: a fixture plugin computing the mean pixel value on a generated
PNG. Failure: decode error → dependents skipped, run `INCOMPLETE`.

## Acceptance Criteria

- [ ] Plugins can declare and read `image.decoded`, `image.working` and `input.original_bytes`.
- [ ] Pixels are decoded at most once per run (verified by a counter in a test).
- [ ] Arrays handed to plugins are read-only.
- [ ] Decode failure results in `SKIPPED` dependents and an `INCOMPLETE` run with the reason recorded.
- [ ] `plugin_api_version` is 1.1.0, and the 1.0.0 fixture plugins still load.

## Expected Outcome

Real forensic plugins can be written against a stable, safe contract.

## Documentation Requirements

Plugin development guide (contracts, context API, examples), CHANGELOG (plugin API
change).

## Dependencies

PP-004, PP-009, PP-011.

## Priority and Effort

P1, medium. This is the gateway issue for every detector.

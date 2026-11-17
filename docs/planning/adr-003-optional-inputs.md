# ADR-003: Optional plugin inputs for fusion normalization

## Status
Accepted

## Context
Issue #32 requires `fusion.normalize` to read detector outputs that are *present* in a run, producing `EvidenceLayer` objects for available detectors and marking absent ones as `unavailable` — never producing zero-score layers.

The plugin metadata currently has a single `inputs` field: every declared input is required. If a plugin declares `requires` on all detectors and any detector fails, the dependent plugin is `SKIP`ped entirely. For `fusion.normalize`, this is unacceptable: a single failing detector must not prevent normalization of the detectors that *did* succeed.

## Decision

Add an `optional_inputs` field to `PluginMetadata` (plugin API 1.1.0 → 1.2.0). `optional_inputs` lists contracts the plugin MAY read if present. The executor:

- Validates only required `inputs` (unchanged behavior: missing required input → SKIP).
- Includes any `optional_inputs` that are present in `run_inputs` or earlier plugin outputs in the plugin's `PluginContext.inputs`.
- Never SKIPs a plugin solely because an optional input is absent.

`fusion.normalize` declares every known detector output as `optional_inputs`, so it always runs and normalizes whatever evidence is available.

## Alternatives considered

**Option B — `requires` on a fixed detector list:** Rejected. If any required detector fails, `fusion.normalize` is SKIPped and no layers are produced at all, defeating the purpose of graceful degradation.

## Consequences

- `plugin_api_version` bumps from 1.1.0 to 1.2.0 (MINOR). API 1.0.0 and 1.1.0 plugins remain compatible (`plugin.minor <= host.minor`).
- `optional_inputs` defaults to `()`; existing plugins are unaffected.
- `optional_inputs` is validated for contract-name format and duplicates, like `inputs`.
- `optional_inputs` must not overlap with `outputs` (reserved run inputs cannot be produced by plugins).

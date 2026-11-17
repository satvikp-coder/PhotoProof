# ADR-005: Evidence sufficiency and inconclusive assessment

## Status
Accepted

## Context
Issue #34 requires `fusion.assessment` to produce the run's overall assessment
category based on coverage (which detectors were available and applicable). The
most dangerous failure for a forensic tool is "nothing found" being read as
"authentic" when detectors did not run or were not applicable.

## Decision

### Assessment categories
- `manipulation_indicated`: strong evidence of manipulation found
- `differences_found` (comparison mode only): differences between reference and target
- `no_indicators_found_with_sufficient_coverage`: sufficient coverage but no manipulation indicators
- `inconclusive`: insufficient coverage, missing detectors, or all detectors failed

There is NO "authentic"/"genuine"/"real" category.

### Coverage matrix
Detector families: compression, noise, visual, geometry, metadata
Each family has state: applicable/available, applicable/unavailable, not_applicable

Sufficient coverage = at least N=2 independent families applicable with
non-low confidence (provisional value, documented, revisited in #37).

### Category logic
1. If coverage < threshold (fewer than 2 families with non-low confidence) → `inconclusive`
2. If comparison mode and differences found → `differences_found`
3. If manipulation indicators present → `manipulation_indicated`
4. If sufficient coverage but no indicators → `no_indicators_found_with_sufficient_coverage`
5. Otherwise → `inconclusive`

### Output
`fusion.assessment`: `{category, reasons[], coverage{family: state}, limitations[]}`

### Plugin contract
- Plugin ID: `fusion.assessment`
- Priority: 95 (after `fusion.rules`)
- Requires: `fusion.rules`
- `after`: all detectors
- Output: `fusion.assessment`

## Alternatives considered
**"authentic" category**: explicitly rejected. No category name may contain
"authentic", "genuine", or "real".

## Consequences
- `plugin_api_version` stays at 1.2.0 (no new contract fields needed)
- Assessment is distinct from RunStatus (pipeline health) — both are reported

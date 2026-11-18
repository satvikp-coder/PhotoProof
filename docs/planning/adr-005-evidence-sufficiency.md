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

### Coverage matrix (corrected implementation)

A family counts only when a medium/strong layer has a nonempty finite 2-D unit-interval
map and a matching boolean valid mask with at least one valid pixel. Weak-only families
are `low_confidence`; invalid/missing output is `applicable_unavailable`; absent families
are `not_applicable`. These states describe provisional evidence coverage, not calibrated
confidence or forensic accuracy. Valid zero anomaly scores can still represent a
measurement; missing measurements cannot substitute for them.

`minimum_families_for_sufficient_coverage` is read from configuration (default 2).
Any declared unavailable output, a family without valid applicable output, insufficient
non-weak coverage, or an absent/invalid fused map yields `inconclusive`. This conservative
policy may return inconclusive despite a suspicious signal; the upstream evidence is
still retained for inspection. Normalized output availability and family diversity are
not guarantees that a forged region would have been detected.

### Category logic

1. Apply the coverage/availability gates above before considering a favorable category.
2. Use the accepted `fusion.map` against configured `manipulation_confidence_threshold`
   (default 0.5). Exceeding it yields `manipulation_indicated`; no raw keypoint score or
   arbitrary text can establish this category.
3. Otherwise emit `no_indicators_found_with_sufficient_coverage`, qualified by the coverage
   table and provisional limitations. This is never an image authenticity claim.
4. `differences_found` remains reserved for the measured comparison contract. Current
   single-image assessment does not infer it from words such as reference or comparison
   in evidence text. Final comparison assessment integration must be verified with #28-#31
   before claiming that category works end to end.

### Correction and migration

Version 1.0.0 ignored configuration, counted invalid/weak maps as sufficient and omitted
`fusion.layers`/`fusion.unavailable` from its declared inputs, so the controller did not
supply the coverage it read. Five deterministic regressions reproduce these gaps. Plugin
version 2.0.0 corrects them; historical manifests stay unchanged. Re-analyze for corrected
assessment and do not compare historical categories without their recorded rule version.
The execution API version remains unchanged; existing optional input contracts are used.

### Output
`fusion.assessment`: `{category, reasons[], coverage{family: state}, limitations[]}`

### Plugin contract
- Plugin ID: `fusion.assessment`
- Priority: 95 (after `fusion.rules`)
- Requires: `fusion.rules`
- Consumes `fusion.layers`, `fusion.unavailable`, `fusion.map`, `fusion.evidence` as optional inputs
- Output: `fusion.assessment`

## Alternatives considered
**"authentic" category**: explicitly rejected. No category name may contain
"authentic", "genuine", or "real".

## Consequences
- `plugin_api_version` stays at 1.2.0 (no new contract fields needed)
- Assessment is distinct from RunStatus (pipeline health) — both are reported

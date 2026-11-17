# ADR-004: Rule-based evidence fusion

## Status
Accepted

## Context
Issue #33 requires `fusion.rules` to combine normalized evidence layers from
`fusion.normalize` (#32) into a fused suspicion map and per-region evidence
summaries using transparent, documented rules. It is not a learned or
probabilistic model — scores are heuristic and must never inflate weak cues
into strong claims.

## Decision

### Rule set (implemented in `fusion.rules`)
1. **Strong evidence** (copy-move masks/verification): can make a region
   suspicious alone. Strong score = max over all strong layer maps.
2. **Medium evidence** (noise inconsistency, DQ map): needs support from at
   least one other independent detector family, or a high score (> 0.8).
3. **Weak evidence** (ELA, sharpness, edges, grid): can only *raise
   confidence* in regions already supported by medium or strong evidence.
   Never marks a region suspicious alone.
4. **Family counting**: each detector family (compression, noise, geometry,
   visual, metadata) counts once toward evidence strength — correlated
   detectors within a family do not double count.

### Fused score per pixel (corrected implementation)

Weights and thresholds are read from the validated plugin configuration. All defaults
are provisional until #37, and scores remain uncalibrated heuristics.

1. Multiply strong maps by `strength_weights.strong` (default 1.0); take their maximum S.
2. Gate each medium map on positive medium/weighted-strong support from another
   family, or its raw score exceeding `medium_high_score_threshold` (default 0.8).
   Multiply accepted maps by `strength_weights.medium` (default 0.6).
3. Gate weak maps on positive **accepted** medium/strong contribution, then multiply
   by `strength_weights.weak * weak_boost_factor` (defaults 0.3 * 0.2).
   A rejected medium cue cannot enable a weak cue. Weak-only output is exactly zero;
   the retained `weak_only_max_score` compatibility setting is therefore an upper
   bound that cannot make weak-only evidence positive.
4. For each family, take the maximum weighted medium/weak contribution F. Where that
   family already has positive strong evidence, set F to zero to prevent double counting.
5. Combine as `fused = 1 - (1 - S) * product(1 - F)` over families. Strong layers
   alone combine by maximum. Repeated correlated cues cannot increase the score.

Only effective winning contributors (ties retained) reach region attribution. Invalid
pixels contribute nothing; malformed/nonfinite/out-of-range maps fail visibly. Inputs
are resampled by nearest neighbour to the smallest common grid without modifying them.
The heuristic formula has no statistical independence or forensic accuracy guarantee.

### Correction and migration

Review found that version 1.0.0 ignored configuration weights, combined correlated
layers separately and gated weak cues on rejected medium inputs. Three deterministic
regressions reproduce these behaviours. The corrected rule semantics use plugin version
2.0.0; implementation/version fingerprints invalidate caches. Historical manifests are
retained unchanged and must be interpreted using their recorded rule version. Re-run
analysis to obtain corrected results; do not compare scores across rule versions as
though calibrated. Unavailable evidence now has `score: null`, never a zero-score cue;
consumers must branch on `reason: unavailable` before interpreting a score.

### Outputs
- `fusion.map`: float32 (H_b, W_b) heuristic suspicion score map ∈ [0, 1].
- `fusion.evidence`: list of per-region dicts, each listing contributing
  detectors, their raw maximum scores and effective weighted contributions, rules
  fired, and families involved. Unavailable entries have null scores.
- `fusion.score_info`: `{label: heuristic_score, calibration: uncalibrated}`.

### Contracts
- Plugin ID: `fusion.rules`
- Priority: 90 (after `fusion.normalize`)
- Requires: `fusion.normalize`
- Outputs: `fusion.map`, `fusion.evidence`, `fusion.score_info`
- Reads `fusion.layers` and `fusion.unavailable` as optional inputs.

### Edge cases
- All detectors unavailable → no fused map (map is None), evidence lists
  unavailable detectors, status passed to sufficiency logic (#34).
- Conflicting evidence (strong copy-move vs nothing else) → reported as-is.
- Resolution mismatch → all layers resampled to a common grid
  (min of layer resolutions).

## Alternatives considered
**Learned/probabilistic fusion**: rejected. The project explicitly requires
explainable, rule-based fusion that never inflates weak cues into strong
claims.

## Consequences
- `plugin_api_version` stays at 1.2.0 (no new contract fields needed;
  `optional_inputs` from #32 suffices).
- `fusion.rules` is not a required plugin; runs fail gracefully if
  `fusion.normalize` did not produce layers.

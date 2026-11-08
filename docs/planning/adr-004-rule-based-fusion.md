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

### Fused score per pixel
```
fused = max(strong_layer_maps)
        + weighted_noisy_or(gated_medium_layers, gated_weak_layers, weights)
```
where `gated_medium_layers` includes a medium layer only if another family has
medium/strong support or the layer's score > 0.8, and `gated_weak_layers`
includes a weak layer only where medium/strong evidence exists.

Noisy-OR: `1 - prod(1 - w_i * s_i)` for each gated layer with weight `w_i`
and score `s_i`. Result clamped to `[0, 1]`.

### Outputs
- `fusion.map`: float32 (H_b, W_b) heuristic suspicion score map ∈ [0, 1].
- `fusion.evidence`: list of per-region dicts, each listing contributing
  detectors, their scores, rules fired, and families involved.

### Contracts
- Plugin ID: `fusion.rules`
- Priority: 90 (after `fusion.normalize`)
- Requires: `fusion.normalize`
- Outputs: `fusion.map`, `fusion.evidence`
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

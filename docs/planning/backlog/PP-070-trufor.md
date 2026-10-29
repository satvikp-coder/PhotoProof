---
id: PP-070
title: "research(plugins): integrate optional TruFor pretrained model and compare with classical detectors"
milestone: M7
labels: [type:research, priority:P3, area:plugins, effort:large, status:needs-design]
depends_on: [PP-012, PP-066, PP-052]
---
## Summary

An optional research plugin `research.trufor` that runs the TruFor forgery localization
model (Guillaro et al., CVPR 2023) on CPU (GPU when available) after a licence review,
and compares it with the classical pipeline. It is not part of the default profiles.

## Problem Statement

Learned detectors outperform classical methods on many benchmarks, but they bring
licensing, reproducibility, explainability and resource concerns. The project needs
evidence before considering them.

## Objective

A research-profile-only plugin, with manual, hash-verified weight installation, plus a
comparative evaluation report.

## Technical Background

TruFor combines RGB with a learned noise-sensitive fingerprint (Noiseprint++) in a
transformer-based architecture. It outputs a localization map, a confidence map and an
integrity score. Its weights are released under a research licence, which must be
reviewed. Inference needs PyTorch (large dependency): put it in an optional dependency
group, never in the default lockfile.

## Functional Requirements

- Weights placed manually in `models/trufor/` and verified by SHA-256 against a value
  recorded in configuration. No automatic download.
- The plugin is unavailable (reported, not crashing) when weights or torch are missing.
- Outputs map onto `EvidenceLayer` (PP-032) with strength `research` and
  `calibration: uncalibrated`, and are excluded from fusion unless the research profile
  enables them.
- Only enabled in the `research` profile.

## Technical Implementation

`src/photoproof/plugins/research/trufor/`. A separate lockfile
`requirements-research.lock` for torch (CPU wheels). Runs under `ProcessExecutor`
(PP-052) with memory limits.

## Integration Requirements

The research profile enables it. The cache key includes the weights' SHA-256 (through
`python_dependencies` and model identity in the metadata).

## Security and Edge Cases

Never load pickled weights from untrusted sources (use the `weights_only` loading mode,
or safetensors if available). Memory use on large images (tile, or downscale with
recording). GPU optional.

## Testing Requirements

The plugin loads and reports unavailability without weights (CI). With weights (manual):
a comparative evaluation via PP-066 on the same splits as the classical detectors.

## Acceptance Criteria

- [ ] Licence review documented before any use.
- [ ] The plugin is unavailable, cleanly, without weights or torch (CI test).
- [ ] Weights are SHA-256 verified before loading, and the pickle-safe loading mode is enforced.
- [ ] Comparative evaluation report published (manual run).

## Expected Outcome

An evidence-based decision on whether learned detection belongs in PhotoProof.

## Documentation Requirements

A research page, the models README, and datasets/licences.

## Dependencies

PP-012, PP-066, PP-052.

## Priority and Effort

P3, large. Optional research.

---
id: PP-068
title: "test(evaluation): evaluate false positives and robustness to benign processing"
milestone: M6
labels: [type:testing, priority:P2, area:testing, effort:medium, status:blocked]
depends_on: [PP-066, PP-033]
---
## Summary

Measure how often PhotoProof flags *authentic* images, and how detections change under
benign processing (recompression, resizing, sharpening, denoising, social-media-like
pipelines), per detector and after fusion.

## Problem Statement

False positives on authentic images are the most harmful failure: they accuse genuine
photographs. Benign processing is ubiquitous and also breaks detectors (for example,
resizing destroys the JPEG grid).

## Objective

An FPR report on authentic images, and robustness curves (metric vs processing strength)
per detector and for fusion.

## Technical Background

Authentic sources: authentic images from CASIA v2 and COVERAGE plus project-owned photos
(licensed). Processing chain generator: JPEG q ∈ {95, 85, 75, 60}; resize ×{0.75, 0.5};
unsharp mask; Gaussian noise; a WhatsApp-like chain (resize to a 1600 px long edge plus
JPEG q≈75). Report the image-level FPR and the mean suspicious-area fraction on authentic
images.

## Functional Requirements

- `tools/evaluate.py robustness --chain …` with seeded, documented processing chains.
- Per-detector and fusion FPR on authentic images, with CIs.
- Robustness curves on forged images (detection F1 vs processing strength).
- Identification of the detectors whose `confidence: low` handling needs improvement.

## Technical Implementation

`tools/processing.py` (chains), the harness extension and a report under
`docs/planning/results/`.

## Integration Requirements

The results feed PP-037 (FPR target) and the detector documentation (false-positive
sections).

## Security and Edge Cases

Do not publish identifiable personal photos. Licence compliance.

## Testing Requirements

The processing chains are deterministic (tests).

## Acceptance Criteria

- [ ] FPR on authentic images reported per detector and for fusion, with CIs.
- [ ] Robustness curves for at least four processing types.
- [ ] Each detector's documentation is updated with the measured false-positive behaviour.

## Expected Outcome

Known false-positive behaviour, and evidence for the confidence and threshold design.

## Documentation Requirements

The results page and detector pages.

## Dependencies

PP-066, PP-033.

## Priority and Effort

P2, medium.

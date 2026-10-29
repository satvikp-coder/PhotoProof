# PhotoProof Project Plan

## Problem statement

Image editing is increasingly accessible, while visual inspection alone may not reveal alterations. PhotoProof explores classical digital-image-forensics techniques and, later, learned segmentation to identify regions that warrant human review.

## Scope for version 0.1

- Load a user-selected image in a local Streamlit interface.
- Generate an Error Level Analysis (ELA) recompression-difference map.
- Generate a high-frequency residual visualization from Gaussian smoothing.
- Explain the limitations of both methods directly in the interface.
- Unit-test output dimensions, modes, and input validation.

This milestone does not claim to identify every manipulated image and does not yet implement copy-move detection, fusion of multiple signals, or a trained localization model.

## Proposed milestones

### M1 — Classical visualization prototype

- [x] ELA visualization
- [x] Noise-residual visualization
- [x] Local interface and initial tests
- [ ] Add export buttons for visualizations

### M2 — Copy-move analysis

- [ ] Implement feature/keypoint detection and descriptor matching.
- [ ] Filter matches and use geometric verification to reduce accidental matches.
- [ ] Visualize candidate matched regions and describe failure cases.
- [ ] Compare with controlled synthetic copy-move examples.

### M3 — Dataset and evaluation

- [ ] Choose a public dataset with authentic images and pixel-level masks; review its license/terms.
- [ ] Build a reproducible loader and image/mask pairing checks.
- [ ] Split by original source image to limit leakage between training and evaluation.
- [ ] Report precision, recall, F1, IoU, runtime, and false-positive behaviour on authentic images.
- [ ] Test robustness to JPEG compression, resizing, and blur.

### M4 — Method comparison and reporting

- [ ] Create a side-by-side result view.
- [ ] Keep method outputs separate and explain their evidence rather than treating them as independent proof.
- [ ] Add a machine-readable results format and an exportable report.

### M5 — Optional learned localization

- [ ] Establish a classical baseline before training a model.
- [ ] Train or adapt a small segmentation model using labelled masks.
- [ ] Evaluate on held-out sources and manipulation types not used during training.
- [ ] Compare results and runtime with classical baselines.

## Evaluation principles

- Report dataset, source split, preprocessing, thresholds, software versions, and random seeds where applicable.
- Include authentic-image false positives as well as manipulated-image results.
- Never use test data to tune thresholds and then report performance on the same test set.
- Treat ELA and residual visualizations as exploratory signals, not proof of manipulation.
- State when the tool cannot reach a useful conclusion.

## Initial stack

Python, OpenCV, NumPy, Pillow, Streamlit, pytest, and (in a later milestone) PyTorch.

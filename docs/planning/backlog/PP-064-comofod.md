---
id: PP-064
title: "feat(evaluation): integrate the CoMoFoD dataset after licensing review"
milestone: M6
labels: [type:research, priority:P2, area:testing, effort:medium, status:needs-design]
depends_on: []
---
## Summary

Review the licence of the CoMoFoD copy-move dataset and add a loader that reads a
locally downloaded copy, verifies checksums, and exposes image/mask pairs with
transformation and post-processing labels.

## Problem Statement

Copy-move evaluation (PP-067) needs a standard benchmark with pixel-level ground truth.

## Objective

`tools/datasets/comofod.py` that, after a manual download, enumerates samples
`{image, mask, category, postprocessing}` reproducibly, with no redistribution.

## Technical Background

CoMoFoD (Tralic et al. 2013, University of Zagreb) has 260 forged image sets in small
(512×512) and large (3000×2000) versions, with transformation categories (translation,
rotation, scaling, distortion, combination) and post-processing variants (JPEG
compression, noise, blur, brightness and contrast changes, colour reduction). Terms of
use must be checked: academic use only, and citation required.

## Functional Requirements

- A licence review recorded in `docs/planning/datasets.md` (who, when, terms, citation).
- Expected local layout documented (`media/datasets/comofod/…`, Git-ignored).
- A checksum manifest (SHA-256 per file, generated on first verified download and
  committed as text).
- The loader yields typed samples, and filtering by category and post-processing.
- A `--subset` option for quick runs.

## Technical Implementation

`tools/datasets/` (outside the `photoproof` package; research tooling). No automatic
download.

## Integration Requirements

Consumed by the evaluation harness (PP-066), the benchmark (PP-067) and calibration
(PP-037).

## Security and Edge Cases

Dataset files are untrusted input, so decode them through the normal decoder path.
Missing files → a clear message listing what is missing. Never commit dataset images.

## Testing Requirements

The loader runs on a tiny synthetic dataset with the same layout (generated in tests).

## Acceptance Criteria

- [ ] Licence review documented, with citation text.
- [ ] Loader works on a local copy and on the synthetic test layout.
- [ ] Checksums are verified and mismatches reported.
- [ ] No dataset content is committed (pre-commit hook and `.gitignore` already enforce this; verify).

## Expected Outcome

A standard copy-move benchmark ready for evaluation.

## Documentation Requirements

`docs/planning/datasets.md`.

## Dependencies

None.

## Priority and Effort

P2, medium. The licensing terms must be confirmed before use.

---
id: PP-065
title: "feat(evaluation): integrate COVERAGE, CASIA v2 and MICC-F220 datasets"
milestone: M6
labels: [type:research, priority:P2, area:testing, effort:medium, status:needs-design]
depends_on: [PP-064]
---
## Summary

Add loaders, licence reviews and checksum manifests for complementary datasets:
COVERAGE (copy-move with similar-but-genuine objects), CASIA v2 (splicing and copy-move),
and MICC-F220 (copy-move, image-level labels).

## Problem Statement

A single dataset overfits evaluation. COVERAGE specifically tests the
repetitive-structure false positives that copy-move detectors suffer from. Splicing
detectors (noise, DCT, ELA) need splicing data.

## Objective

Uniform loaders sharing the PP-064 interface, each with documented licensing,
ground-truth format and known quality issues.

## Technical Background

- **COVERAGE** (Wen et al. 2016): 100 originals and 100 forgeries in which the forged
  region copies a *similar-looking genuine object*.
- **CASIA v2** (Dong et al. 2013): about 7.5k authentic and 5k tampered images. Masks
  are not official (community-derived masks exist, with known errors), so document this.
- **MICC-F220** (Amerini et al. 2011): 220 images, image-level labels only.

## Functional Requirements

- One loader per dataset with the common `Sample` type (mask optional).
- A licence review per dataset in `docs/planning/datasets.md`.
- Known issues documented (e.g. CASIA mask provenance, duplicated sources).
- A source-image grouping key for leakage-free splits (PP-037).

## Technical Implementation

`tools/datasets/{coverage,casia2,micc_f220}.py`, sharing the base from PP-064.

## Integration Requirements

PP-066, PP-067, PP-068, PP-037.

## Security and Edge Cases

Untrusted files. Inconsistent naming between versions. Missing masks (image-level
evaluation only).

## Testing Requirements

Synthetic layouts per dataset in tests.

## Acceptance Criteria

- [ ] Three loaders implemented with licence reviews and checksums.
- [ ] Each dataset's ground-truth limitations are documented.
- [ ] A source grouping key is provided for every dataset.

## Expected Outcome

Diverse, documented evaluation data.

## Documentation Requirements

`docs/planning/datasets.md`.

## Dependencies

PP-064 (shared loader base).

## Priority and Effort

P2, medium.

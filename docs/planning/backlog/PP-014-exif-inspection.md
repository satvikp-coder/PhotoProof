---
id: PP-014
title: "feat(plugins): inspect EXIF and container metadata for provenance inconsistencies"
milestone: M2
labels: [type:feature, priority:P1, area:plugins, effort:medium, status:blocked]
depends_on: [PP-012]
---
## Summary

Plugin `metadata.exif` extracts EXIF, XMP and container metadata, and reports
documented *inconsistency findings* (for example, editing-software tags or thumbnail
mismatches) as low-weight provenance evidence.

## Problem Statement

Metadata frequently reveals processing history, but it is trivially editable and often
stripped. It must be reported as context, never as proof.

## Objective

Structured metadata output plus a list of typed findings, each with a fixed severity
and a false-positive note.

## Technical Background

EXIF (CIPA DC-008) lives in JPEG APP1, PNG `eXIf`, WebP `EXIF`, and HEIF `Exif` items.
XMP is RDF/XML in APP1 or an `iTXt` chunk. Useful checks:

- `Software` / `ProcessingSoftware` / XMP `CreatorTool` naming an editor;
- `DateTimeOriginal` later than `DateTime` (modify), or impossible dates;
- the embedded JPEG thumbnail differs strongly from a downscaled main image (the
  thumbnail was not regenerated after editing). Compare with a perceptual hash
  (dHash) Hamming distance;
- `PixelXDimension`/`PixelYDimension` disagreeing with the actual dimensions;
- maker-note presence or absence relative to `Make`.

## Functional Requirements

- Output `metadata.summary`: normalized dict (camera, lens, timestamps, software, GPS
  presence as a boolean — not the coordinates — and dimensions).
- Output `metadata.findings`: list of `{code, severity: info|low|medium, message,
  fields, false_positive_note}`.
- Raw GPS coordinates are excluded from outputs, logs and reports by default (privacy).
- Missing metadata → finding `metadata_absent` (info), explicitly *not* suspicious by
  itself.

## Technical Implementation

- Library choice in the PR: Pillow `getexif()` plus a small XMP parser using
  `defusedxml`. Record the security reasoning, because XMP is XML (XXE, billion laughs).
- Plugin package `src/photoproof/plugins/metadata/exif/` (`__init__.py`, `processor.py`,
  `schema.py` with thresholds such as `thumbnail_hamming_threshold`).

## Integration Requirements

- Plugin ID `metadata.exif`; priority 10; requires: none (uses run inputs); inputs:
  `input.original_bytes`, `image.working`; outputs: `metadata.summary`,
  `metadata.findings`.
- Applicable to all formats. If there is no metadata container, return empty findings
  plus `metadata_absent`.
- Failure behaviour: parsing errors in one block are recorded as findings
  (`metadata_malformed`); the plugin still succeeds.
- Versioning: new finding codes are a MINOR plugin bump; changed semantics are MAJOR.

## Security and Edge Cases

XXE and entity expansion in XMP (use defusedxml, and test it). Enormous EXIF blocks;
IFD loops; non-UTF-8 strings; thumbnails that themselves need safe decoding (decode the
thumbnail through the isolated decoder).

## Testing Requirements

Fixtures generated with Pillow (EXIF written programmatically): an editor `Software`
tag, a stale thumbnail, a dimension mismatch, stripped metadata, an XMP bomb.

## Acceptance Criteria

- [ ] Each documented finding code has a positive and a negative test.
- [ ] XMP entity-expansion and XXE payloads are neutralized (test).
- [ ] GPS coordinates never appear in outputs, logs or manifests.
- [ ] `metadata_absent` is documented and reported as informational only.

## Expected Outcome

Investigators see provenance clues with explicit caveats.

## Documentation Requirements

A detector page in `docs/plugins/` with every finding code, its meaning and its
false-positive scenarios.

## Dependencies

PP-012.

## Priority and Effort

P1, medium.

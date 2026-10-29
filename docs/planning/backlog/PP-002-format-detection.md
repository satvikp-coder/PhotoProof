---
id: PP-002
title: "feat(imaging): detect image formats from magic bytes and register decoder adapters"
milestone: M1
labels: [type:feature, priority:P1, area:imaging, effort:medium, status:blocked]
depends_on: [PP-001]
---
## Summary

Implement `imaging/formats.py` (signature-based format identification) and a decoder
registry that selects an `ImageDecoder` adapter for each supported format, governed by
`settings.formats.accepted`.

## Problem Statement

`InputDescriptor.media_format` is always `None`. File extensions are untrustworthy, so
the pipeline cannot enforce `supported_formats`, reject unsupported inputs, or choose a
decoder.

## Objective

A deterministic, dependency-free `detect_format(header: bytes) -> str | None`, plus a
`DecoderRegistry` that maps formats to adapters and reports codec availability.

## Technical Background

Formats are identified by their signatures: JPEG `FF D8 FF`; PNG `89 50 4E 47 0D 0A 1A
0A`; WebP `RIFF....WEBP`; ISO-BMFF `ftyp` boxes with brands `heic`/`heix`/`mif1`
(HEIF) and `avif`/`avis` (AVIF); JPEG XL codestream `FF 0A` or container
`00 00 00 0C 4A 58 4C 20 0D 0A 87 0A`; TIFF `II*\0`/`MM\0*`. The ISO-BMFF brand
must be read from the `ftyp` major and compatible brands, not guessed.

## Functional Requirements

- `detect_format` inspects at most the first 64 bytes and returns one of
  `jpeg png webp heif avif jxl tiff` or `None`.
- `DecoderRegistry.register(decoder)`, `.for_format(fmt)`, `.available()`, and
  `.library_versions()`.
- A format that is detected but disabled in `formats.accepted` is rejected with
  `InputRejectedError("format X is not accepted by settings.formats.accepted")`.
- A format that is accepted but has no available decoder is reported as
  *unsupported on this platform*, with the missing package named.
- `media_format` is filled in on `InputDescriptor` at ingestion.

## Technical Implementation

- `src/photoproof/imaging/formats.py`: pure functions, no third-party imports.
- `src/photoproof/imaging/registry.py`: `DecoderRegistry` holding `ImageDecoder`
  instances (protocol in `imaging/decoder.py`).
- Builds `DecodeLimits` from `settings.resources` (`max_input_bytes`,
  `max_image_pixels`).
- Add health check `imaging.codecs`: WARNING listing accepted formats without decoders.

## Integration Requirements

- Called by the evidence/ingestion path (PP-001, PP-011) right after hashing.
- The executor gains enforcement of `PluginMetadata.supported_formats`: a plugin whose
  list is non-empty and excludes the input format is `NOT_APPLICABLE`. This is a
  backward-compatible behaviour addition (MINOR).

## Security and Edge Cases

- Truncated headers (fewer than 12 bytes) → `None`, never an exception.
- Polyglot files that match several signatures: the first match in a documented,
  fixed order wins, and the decoder still validates the full structure (PP-003).
- An extension mismatch (e.g. `.png` containing JPEG) is logged as an informational
  provenance note, not an error.

## Testing Requirements

- Table-driven unit tests with minimal synthetic headers for every format, including
  truncated, empty and random bytes.
- A property test: random 64-byte inputs never raise.
- Registry tests for disabled formats and missing decoders.

## Acceptance Criteria

- [ ] `detect_format` identifies all seven formats from synthetic headers and returns `None` for random data.
- [ ] Disabled formats are rejected with an actionable message.
- [ ] `doctor` lists accepted formats that have no decoder.
- [ ] `supported_formats` is enforced by the executor (`NOT_APPLICABLE`) and tested.
- [ ] `InputDescriptor.media_format` is populated for every ingested input.

## Expected Outcome

Every input has a trustworthy format label, and the system knows which codecs are
available before decoding anything.

## Documentation Requirements

`docs/configuration/settings.md` (`formats.accepted` is no longer reserved),
`docs/plugins/index.md` (`supported_formats` enforcement), and a new
`docs/planning/index.md` format-support table.

## Dependencies

PP-001 (evidence store provides the bytes and descriptor).

## Priority and Effort

P1, medium. Prerequisite for all decoders.

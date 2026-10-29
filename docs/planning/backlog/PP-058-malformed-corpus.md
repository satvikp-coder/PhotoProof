---
id: PP-058
title: "test(security): build a malformed and adversarial image corpus for decoder regression tests"
milestone: M6
labels: [type:testing, priority:P1, area:security, area:testing, effort:medium, status:blocked]
depends_on: [PP-003, PP-009]
---
## Summary

Create a generated (not downloaded) corpus of malformed and adversarial image files, plus
a regression suite asserting that every decoder rejects or safely handles each file
within time and memory bounds.

## Problem Statement

Decoders face hostile input. Without a corpus, regressions in error handling, limit
enforcement or isolation go unnoticed.

## Objective

`tests/fixtures/malformed/` generators plus a `pytest -m adversarial` suite run in CI.

## Technical Background

Classes of malformed input:

- truncation at every structural boundary;
- invalid lengths (chunk, segment, box sizes);
- decompression bombs (huge dimensions with tiny data; highly compressible IDAT; zlib
  bombs);
- recursive or looping structures (TIFF IFD loops, ISO-BMFF boxes referencing
  themselves);
- polyglots (valid JPEG + ZIP; PNG + HTML);
- metadata attacks (EXIF IFD loops, XMP entity expansion, oversized ICC);
- type confusion (extension vs content).

**Threat model:** an attacker controls all input bytes and aims for code execution,
denial of service, or forcing a misleading result through a parser differential.

## Functional Requirements

- Deterministic generators (seeded) produce the files at test time. Commit only the
  generator code, plus at most a few tiny hand-crafted files with a README explaining
  each.
- Expected outcome per case: `InputRejectedError` with a category, or a successful decode
  with documented behaviour. Never a crash, a hang (time limit) or memory beyond the
  limit.
- Coverage table: format × attack class.

## Technical Implementation

`tests/fixtures/malformed/generate.py`; `tests/security/test_decoders_adversarial.py`
with a `pytest.mark.adversarial` marker (registered in `pyproject.toml`).

## Integration Requirements

Runs in CI (security workflow). New decoders (PP-006–PP-008) must add their cases.
Fuzzing (PP-061) feeds discovered crashes back as regression cases.

## Security and Edge Cases

Do not commit real malware samples. Mark polyglot fixtures clearly. Run the suite under
the isolated decoder (PP-009) and in-process to compare.

## Testing Requirements

This issue is a testing deliverable: at least 5 cases per supported format and attack
class where applicable.

## Acceptance Criteria

- [ ] Corpus generators cover every attack class for each supported format.
- [ ] Every case completes within the time and memory limits, with the expected outcome (CI).
- [ ] The coverage table is documented.
- [ ] No binary fixture is committed without a provenance README.

## Expected Outcome

Decoder robustness is continuously verified.

## Documentation Requirements

Security model (testing section), and the testing page.

## Dependencies

PP-003, PP-009.

## Priority and Effort

P1, medium.

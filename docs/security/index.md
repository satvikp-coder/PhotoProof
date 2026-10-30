# Security model

## Threat model

| Asset | Threat | Current control | Planned |
|---|---|---|---|
| Original evidence | Silent modification | Stored before any processing; content-addressed, read-only; verified on every read; never overwritten or auto-repaired | — |
| Host system | Malicious image exploiting a native decoder | Decoder interface with mandatory limits; Pillow decoder, not yet used by the pipeline | Isolated decoder process with resource limits; fuzzing |
| Host system | Decompression bomb / resource exhaustion | `resources.max_input_bytes` enforced while streaming; `max_image_pixels` contract | Header-level pixel checks, memory limits |
| Runtime data | Path traversal | `safe_join` on every runtime path | — |
| Configuration | Code execution through config | Safe YAML loader, strict schemas, no dynamic imports from config | — |
| Plugin policy | Unauthorized bypass of the blacklist | Local-CLI-only, settings-gated, forbidden in production, audited | WebKit authentication |
| Supply chain | Tampered dependencies | Hash-pinned lockfiles, `--only-binary`, pip-audit, Dependabot, pinned action SHAs; `doctor` reports unlocked packages | Automated relocking (#78), exact venv sync (#83) |
| Results | Edited manifests or audit entries | Manifests verified against the DB hash | Hash-chained audit trail (#81) |
| Runtime data on Windows | Other local users reading evidence | — (POSIX modes only) | Owner-only ACLs (#80) |
| Users' privacy | Retained uploads and metadata | Runtime data is local and Git-ignored | Retention controls, metadata-free previews |

## Trust boundaries and assumptions

- **Untrusted:** every input image and reference image, and anything derived from it
  before validation. These enter only through `IngestionService`, which stages them in
  quarantine, enforces the size cap while reading, identifies the type from content, and
  audits every rejection ([details](../operations/runtime.md#ingestion)).
- **Trusted:** repository code, including plugins under `photoproof.plugins`. Plugin
  discovery is **not** a sandbox. Configuration cannot add plugin locations.
- **Semi-trusted:** configuration files edited by local operators. They are validated
  strictly but assumed not to be hostile.
- The machine is single-user. `ARE/` is owner-only on POSIX.

## Runtime isolation plan

Nothing is isolated yet beyond the policy checks above. In order:

1. A decoder worker process that receives bytes plus `DecodeLimits` and returns
   pixels, with OS resource limits and timeouts.
2. A process-isolated executor implementing the `Executor` protocol, so native-code
   crashes in plugins cannot take down the application.
3. Hardening: seccomp/sandbox profiles where the platform supports them, and dropping
   network access in worker processes.

A module named `sandbox.py` that does none of these would be misleading, so none
exists.

## Decoders

`imaging/adapters/pillow.py` decodes JPEG, PNG and WebP. It treats every file as hostile:

- **Only the detected format's parser runs.** A file can't reach Pillow's other parsers.
- **Size is checked from the header,** before any pixel is allocated. Width × height × frames
  must fit `resources.max_image_pixels`, and an animation is rejected by default.
- **Truncation is rejected,** even when only the end of the file is missing and the pixels
  are complete. PNG checksums are verified for every chunk, including `IDAT`, which Pillow
  skips. Data after the end of a PNG is allowed.
- **Nothing is applied silently.** The ICC profile and EXIF orientation are returned, not
  applied, and `DecodedImage.conversion` records any change of pixel mode.

**Known limits:** decoding runs in the calling process with no wall-clock limit until #9.
16-bit colour PNGs are read as 8-bit, recorded as `16_bit_to_8_bit`, until #5. CMYK JPEGs are
converted naively and recorded as `cmyk_to_rgb`.

## Dependency security

Universal lockfiles with hashes for every artifact; binary wheels only; weekly
pip-audit, Bandit, CodeQL and TruffleHog in CI; Dependabot for Python and Actions;
GitHub Actions pinned to commit SHAs.

## Privilege restrictions

The launcher never escalates privileges or runs shell strings (`subprocess` with
argument lists only). Runtime directories are 0700; blobs are 0400. `GITHUB_TOKEN`
permissions default to `contents: read`; only the release job can write.

## Evidence preservation

Originals are never modified or replaced. `analyze` stores each input before any
processing, and every read re-hashes it first (see the
[evidence store](../operations/runtime.md#evidence-store)). Derived outputs are separate
content-addressed artifacts, and manifests record every input and artifact hash.
A corrupt or missing evidence blob is a MANUAL critical finding.

Report vulnerabilities privately: see
[SECURITY.md](https://github.com/satvikp-coder/PhotoProof/blob/main/SECURITY.md).

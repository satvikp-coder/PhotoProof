# Security model

## Threat model

| Asset | Threat | Current control | Planned |
|---|---|---|---|
| Original evidence | Silent modification | Stored before any processing; content-addressed, read-only; verified on every read; never overwritten or auto-repaired | Upload handling (#11) |
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
  before validation.
- **Trusted:** repository code, including plugins under `photoproof.plugins`. Plugin
  discovery is **not** a sandbox. Configuration cannot add plugin locations.
- **Semi-trusted:** configuration files edited by local operators. They are validated
  strictly but assumed not to be hostile.
- The machine is single-user. `ARE/` is owner-only on POSIX.

## Runtime isolation plan

Decoders and plugins still run in-process. `core/isolation.py` provides the building block
for changing that: `run_isolated` runs a callable in a spawned child process with limits and
returns its result or a typed failure. Nothing uses it yet. In order:

1. A decoder worker process that receives bytes plus `DecodeLimits` and returns
   pixels, built on `run_isolated` (#9).
2. A process-isolated executor implementing the `Executor` protocol, so native-code
   crashes in plugins cannot take down the application.
3. Hardening: seccomp/sandbox profiles where the platform supports them, and dropping
   network access in worker processes.

A module named `sandbox.py` that does none of these would be misleading, so none
exists.

### Child-process runner

| Limit | Linux | macOS | Windows |
|---|---|---|---|
| Wall clock | Parent deadline | Parent deadline | Parent deadline |
| Memory | `RLIMIT_AS` | RSS watchdog, polled every 100 ms | Job Object |
| CPU time | `RLIMIT_CPU` | `RLIMIT_CPU` | Not enforced; the wall clock still applies |

`python main.py --doctor` reports this as `isolation.capabilities`. macOS ignores
`RLIMIT_AS`, so its watchdog can overshoot between polls, and it counts resident memory only.

The child is treated as compromised:

- **Results are plain data, and not pickle.** Replies use `core/plaindata.py`, because crafted
  pickle can make the reader allocate gigabytes even with class lookups refused. Decoding is
  linear in the reply's size, with caps on object count and nesting. Dict keys and set items
  must be strings or bytes, because integer hashes can be made to collide. Anything else is
  `invalid_result`.
- **The deadline cannot be blocked.** Pipe I/O runs off the main thread, so a child that sends
  half a message and hangs still times out. Transfers have size caps in both directions.
- **Nothing survives.** Each child leads its own process group (POSIX) or Job Object (Windows),
  so its descendants die with it. A child that ignores `SIGTERM` is killed. If the parent dies,
  a small watcher process that the parent started kills the whole group at once, even if the
  worker is stuck in native code (POSIX). On Windows the Job Object closes with the parent.
- **Little is inherited.** The child gets a minimal environment, a temporary working directory
  and no core dumps. It receives no work until its limits are in place, and if a limit cannot
  be applied, it does not run.

This limits resources and contains crashes. It is **not** a sandbox: the child can still read
files and use the network as your user until #59.

**Overhead**, measured on an Apple M4 with Python 3.13 (median of 15 calls): about 100 ms per
call, mostly starting the interpreter, and about 380 ms for a 100 MiB round trip. Results are
copied through a pipe, so #9 should measure this against real decoding before adding shared
memory.

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

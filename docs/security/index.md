# Security model

## Threat model

| Asset | Threat | Current control | Planned |
|---|---|---|---|
| Original evidence | Silent modification | Content-addressed, read-only storage; integrity check; never auto-repaired | Ingestion that stores originals before any processing |
| Host system | Malicious image exploiting a native decoder | Decoder interface with mandatory limits; no decoder yet | Isolated decoder process with resource limits; fuzzing |
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

- **Results are plain data.** The parent's unpickler refuses to resolve any class or function,
  so a forged reply cannot run code. A result containing anything else is `invalid_result`.
- **The deadline cannot be blocked.** Pipe I/O runs off the main thread, so a child that sends
  half a message and hangs still times out. Transfers have size caps in both directions.
- **Nothing survives.** Each child leads its own process group (POSIX) or Job Object (Windows),
  so its descendants die with it. A child that ignores `SIGTERM` is killed, and a child whose
  parent died stops itself shortly after its deadline (POSIX) or with the Job Object (Windows).
- **Little is inherited.** The child gets a minimal environment, a temporary working directory
  and no core dumps. It receives no work until its limits are in place, and if a limit cannot
  be applied, it does not run.

This limits resources and contains crashes. It is **not** a sandbox: the child can still read
files and use the network as your user until #59.

**Overhead**, measured on an Apple M4 with Python 3.13 (median of 15 calls): about 105 ms per
call, mostly starting the interpreter, and about 410 ms for a 100 MiB round trip. Results are
copied through a pipe, so #9 should measure this against real decoding before adding shared
memory.

## Dependency security

Universal lockfiles with hashes for every artifact; binary wheels only; weekly
pip-audit, Bandit, CodeQL and TruffleHog in CI; Dependabot for Python and Actions;
GitHub Actions pinned to commit SHAs.

## Privilege restrictions

The launcher never escalates privileges or runs shell strings (`subprocess` with
argument lists only). Runtime directories are 0700; blobs are 0400. `GITHUB_TOKEN`
permissions default to `contents: read`; only the release job can write.

## Evidence preservation

Originals are never modified or replaced. Derived outputs are separate
content-addressed artifacts, and manifests record every input and artifact hash.
A corrupt evidence blob is a MANUAL critical finding.

Report vulnerabilities privately: see
[SECURITY.md](https://github.com/satvikp-coder/PhotoProof/blob/main/SECURITY.md).

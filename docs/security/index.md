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
| Supply chain | Tampered dependencies | Hash-pinned lockfiles, `--only-binary`, pip-audit, Dependabot, pinned action SHAs | — |
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

Nothing is isolated yet beyond the policy checks above. In order:

1. A decoder worker process that receives bytes plus `DecodeLimits` and returns
   pixels, with OS resource limits and timeouts.
2. A process-isolated executor implementing the `Executor` protocol, so native-code
   crashes in plugins cannot take down the application.
3. Hardening: seccomp/sandbox profiles where the platform supports them, and dropping
   network access in worker processes.

A module named `sandbox.py` that does none of these would be misleading, so none
exists.

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

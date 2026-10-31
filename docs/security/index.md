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
| Runtime data on Windows | Other local users reading/changing runtime data | Protected SID allowlist, native DACL verification and SAFE repair on local ACL-capable volumes | Authentication and broader isolation remain separate concerns |
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

A bad ICC profile can't stop an analysis or reach the result silently: `normalize` catches the
failure, leaves the pixels unconverted, and records `icc_invalid`. Profiles over 4 MiB are ignored
and recorded. LittleCMS still parses the rest in-process until #9.

**Known limits:** decoding runs in the calling process with no wall-clock limit until #9.
Interlaced 16-bit colour PNGs and 16-bit gray with alpha are read as 8-bit, and recorded as
`16_bit_to_8_bit`. 16-bit PNGs with a transparency key are rejected, because the transparency
can't be represented or would come out wrong. CMYK JPEGs are converted naively and recorded as `cmyk_to_rgb`.
Memory is estimated from the header and checked against `DecodeLimits.max_decoded_bytes` (2 GiB,
not yet configurable) before any pixel is decoded.

## Dependency security

Universal lockfiles with hashes for every artifact; binary wheels only; weekly
pip-audit, Bandit, CodeQL and TruffleHog in CI; Dependabot for Python and Actions;
GitHub Actions pinned to commit SHAs.

## Privilege restrictions

The launcher never escalates privileges or runs shell strings (`subprocess` with
argument lists only). Runtime directories are 0700; blobs are 0400. `GITHUB_TOKEN`
permissions default to `contents: read`. Only the release job can write contents, and the
milestone job can write pull requests.

### Windows runtime ACL boundary

On local volumes reporting `FS_PERSISTENT_ACLS` (normally NTFS/ReFS), directories
receive a protected DACL **at creation**, rather than inheriting broad parent access
and restricting it afterward. Exactly these principals are approved:

- The process token's **TokenUser SID** (local or domain account), full control.
- LocalSystem (`S-1-5-18`), full control for service/backup access.
- Built-in Administrators (`S-1-5-32-544`), full control for administrative recovery.

There are no grants to Everyone, Users or Authenticated Users. No username parsing,
localized account lookup, shell command, privilege enabling or ownership takeover is
used in production. New directory ownership is explicitly the token user. Existing
ownership must already belong to the user, SYSTEM or Administrators; other owners
require explicit operator-managed migration, never an automatic takeover.

`runtime.permissions` inspects actual owners, DACL control flags, ACE types, SIDs,
rights and file attributes. It checks the root and directories/files in the ten
managed runtime trees, including nested hash/cache directories. Unauthorized inherited
grants are findings, just like explicit grants. Root/top-level DACLs must be protected;
canonical inherited grants inside those boundaries are allowed. NULL DACLs,
unknown/conditional/object ACEs and uninspectable permissions never count as healthy.
The verifier requires a canonical policy, not a partial effective-access calculation
for arbitrary ACLs.

SAFE repair changes security metadata only on enumerated managed paths and rechecks
the result. Directory handles use `MAXIMUM_ALLOWED` to prevent Win32
`SetSecurityInfo` from automatically propagating changes to children, according to
[Microsoft's documented handle behavior](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo).
No recursive ACL command is used. Unknown entries directly under the runtime root and
all paths outside it are not repermissioned. Reparse points/junctions inside the resolved runtime, redirected
handles and hard links are refused so metadata operations cannot deliberately reach
an unrelated target. Runtime ancestors must themselves be trusted: this is not a
sandbox against an administrator concurrently changing ancestor paths or retaining
already-open handles.

Insecure Windows permissions are an **error**, blocking default preflight until
repaired. An operation error or failed readback is reported, without a permissive
fallback or ACL rollback that would reopen access. Unsupported filesystems, UNC
paths and mapped network drives produce a **warning/manual finding**; diagnostics
may run, but ACL confidentiality is not claimed and blob/evidence writes are refused
before bytes are published. Logs/database operations on such a store are not
certified private. Move the runtime to a trusted local ACL-capable volume. Actual
SMB server policies, remote credentials and network ACL fidelity are outside the
guarantee, even if a server advertises ACL support.

## Evidence preservation

Originals are never modified or replaced. `analyze` stores each input before any
processing, and every read re-hashes it first (see the
[evidence store](../operations/runtime.md#evidence-store)). Derived outputs are separate
content-addressed artifacts, and manifests record every input and artifact hash.
A corrupt or missing evidence blob is a MANUAL critical finding.

On Windows, newly published blobs receive `FILE_ATTRIBUTE_READONLY` and a protected
DACL granting **read only** (`FILE_GENERIC_READ`) to the same three principals.
Normal new opens cannot write data or clear the read-only attribute. Sealing is
verified before a successful hash is returned; failed sealing leaves bytes intact
and reports failure. Existing valid blobs must already be sealed on reuse; broadened
ACLs are reported rather than silently repaired by a write. Permission repair
can restore blob metadata but never replaces bytes or repairs a hash mismatch.
Security metadata changes may change filesystem metadata timestamps.

Ordinary writes verify the root, staging and destination directories without repairing
existing ACLs. Repairs belong to the audited SAFE health path. Publication through one
`RuntimeStorage` instance is serialized so its concurrent writers cannot observe a
half-sealed blob. Across instances/processes, an incompletely sealed blob can cause a safe, retryable
permission failure; no existing original is replaced. Unsupported Windows storage is
rejected before the evidence store creates a staging file.

If a privileged actor removes the read-only attribute while leaving the read-only
DACL sealed, the process may lack `WRITE_ATTRIBUTES` to restore it. This is detected
and repair reports access denied; it does not temporarily broaden the blob DACL to
obtain that right. Explicit owner-authorized metadata repair is then required.

This is **not WORM storage**. Owners can explicitly change their DACLs; privileged
administrators/SYSTEM can recover ownership or use backup privileges, and existing
handles may retain rights granted before sealing. Hash and manifest verification
remain necessary. A read-only bit alone is not access control or tamper-proofing.
Existing POSIX subdirectory `0700` and blob `0400` behavior remains unchanged; the
POSIX runtime root's creation mode still follows its existing parent/umask behavior.

Report vulnerabilities privately: see
[SECURITY.md](https://github.com/satvikp-coder/PhotoProof/blob/main/SECURITY.md).

## Permission scan and initialization

Layout creation checks existing ACLs without repairing them. Broad permissions remain
a health finding until explicit/SAFE permission repair. Permission scans check the
local volume once, reuse directory snapshots within that scan, and still inspect every
managed file's actual ACL; no results are cached across scans. Windows findings block
startup deliberately, because unreadable or unsupported native descriptors cannot be
assumed private; POSIX warning behavior is preserved for compatibility.

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
| Runtime data on Windows | Other local users reading/changing runtime data | Protected SID allowlist, native DACL verification and SAFE repair on local ACL-capable volumes | Authentication and broader isolation remain separate concerns |
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
all paths outside it are not repermissioned. Reparse points/junctions, redirected
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

Originals are never modified or replaced. Derived outputs are separate
content-addressed artifacts, and manifests record every input and artifact hash.
A corrupt evidence blob is a MANUAL critical finding.

On Windows, newly published blobs receive `FILE_ATTRIBUTE_READONLY` and a protected
DACL granting **read only** (`FILE_GENERIC_READ`) to the same three principals.
Normal new opens cannot write data or clear the read-only attribute. Sealing is
verified before a successful hash is returned; failed sealing leaves bytes intact
and reports failure. Existing valid blobs are sealed on reuse. Permission repair
can restore blob metadata but never replaces bytes or repairs a hash mismatch.
Security metadata changes may change filesystem metadata timestamps.

This is **not WORM storage**. Owners can explicitly change their DACLs; privileged
administrators/SYSTEM can recover ownership or use backup privileges, and existing
handles may retain rights granted before sealing. Hash and manifest verification
remain necessary. A read-only bit alone is not access control or tamper-proofing.
Existing POSIX subdirectory `0700` and blob `0400` behavior remains unchanged; the
POSIX runtime root's creation mode still follows its existing parent/umask behavior.

Report vulnerabilities privately: see
[SECURITY.md](https://github.com/satvikp-coder/PhotoProof/blob/main/SECURITY.md).

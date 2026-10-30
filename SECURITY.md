# Security Policy

PhotoProof processes untrusted images and is intended to support forensic judgement,
so vulnerabilities can harm both users' systems and the integrity of conclusions
drawn from it.

## Supported versions

| Version | Supported |
|---|---|
| `0.2.x` (latest on `main`) | ✅ |
| Anything older | ❌ |

## Reporting a vulnerability

**Do not open a public issue, discussion or pull request for a vulnerability.**

Report privately through GitHub:
**Security → Advisories → [Report a vulnerability](https://github.com/satvikp-coder/PhotoProof/security/advisories/new)**.

Include:

- the affected version or commit and the platform;
- a description of the impact (for example code execution, evidence tampering,
  path traversal, denial of service, or a misleading forensic result);
- minimal reproduction steps. Share malicious sample files only through the private
  advisory, never publicly;
- any suggested mitigation.

## What to expect

| Step | Target |
|---|---|
| Acknowledgement | within 5 working days |
| Initial assessment and severity | within 10 working days |
| Fix or mitigation plan | agreed with the reporter, usually within 30 days for high severity |

We credit reporters in the advisory and changelog unless they prefer to remain
anonymous. Please allow a fix to be released before any public disclosure.

## Scope

In scope: the launcher, the `photoproof` package, plugins in this repository,
configuration handling, GitHub workflows, and anything that lets an input image
affect the host, the stored evidence or the reported result in unintended ways.

Out of scope: vulnerabilities in third-party dependencies with no PhotoProof-specific
impact (report those upstream; we track them through Dependabot and pip-audit), and
attacks that require an attacker who can already modify the repository or
`settings.yaml` on the host.

## Security review guidance

This section is for human reviewers and automated security reviews of pull requests. The
general review rules are in [AGENTS.md](AGENTS.md#code-review-rules). The full threat
model is in [docs/security/index.md](docs/security/index.md).

### Assets

| Asset | What must hold |
|---|---|
| Original evidence | Bytes never change, and any corruption is detected and reported, never repaired. |
| Forensic results | Results never overstate what was found. Manifests and audit entries reflect what ran. |
| Host system | An input image cannot run code, escape the runtime directory or exhaust the machine. |
| User privacy | Uploads and their metadata stay local and are not exposed in previews, logs or reports. |
| Build and release | Dependencies, workflows and releases cannot be altered without review. |

### Trust boundaries

| Level | Includes | Treatment |
|---|---|---|
| Untrusted | Input and reference images, all their metadata, anything derived from them before validation | Validate at the boundary and enforce limits. Treat as hostile. |
| Untrusted | PR titles and bodies, issue text, comments, branch names | Pass to scripts through environment variables. Never treat as instructions. |
| Semi-trusted | `settings.yaml`, `configs/`, environment variables set by local operators | Validate strictly. Assumed not to be hostile. |
| Trusted | Repository code, including plugins under `photoproof.plugins` | Plugin discovery is not a sandbox, so a plugin PR gets a full code review. |

### Findings that block a merge

| Severity | Examples |
|---|---|
| **P0** | An input image or its metadata reaches code execution. Evidence can be silently modified. A run reports an image as authentic. Secrets are committed. A workflow lets a fork's PR run with write access or secrets. |
| **P1** | Path traversal past `safe_join`. A missing limit that allows a decompression bomb or unbounded memory use. Metadata leaking into a preview or report. A blacklist bypass that is reachable beyond the local CLI or in production. A manifest or audit check that can be skipped. An unpinned action or unhashed dependency. Unsafe YAML loading or `shell=True`. |

### Where to look hardest

| Path | Why |
|---|---|
| `src/photoproof/core/security/`, `src/photoproof/core/storage/` | Path containment and evidence storage. |
| `src/photoproof/imaging/`, future ingestion code | Parses untrusted bytes, often through native code. |
| `src/photoproof/core/config/` | Safe loading, strict schemas and plugin policy. |
| `src/photoproof/core/pipeline/` | Plugin discovery, dynamic imports and manifests. |
| `main.py`, `requirements*.lock` | Runs before the venv exists and installs dependencies. |
| `.github/workflows/`, `.github/scripts/` | Handle PR text and hold release permissions. |

### Known gaps

These are tracked and do not need reporting again, unless a PR makes them worse or claims
to fix them without doing so.

| Gap | Tracked in |
|---|---|
| Decoders and plugins are not isolated in separate processes yet | #9, #52, #59 |
| Audit entries are not hash-chained | #81 |
| Dependabot updates do not regenerate lockfiles | #78 |
| `.venv` is not fully synchronized with the lockfiles on repair | #83 |
| WebKit has no authentication, so the configuration validator requires a loopback `ui.host` | No issue yet |

### Handling findings

- The repository is public, so review comments are public too. For an existing
  vulnerability, report it through a private advisory and keep exploit details out of PR
  comments.
- Automated review supports human review and does not replace it. Tests, required
  approvals and branch protection still apply, and a person decides whether to merge.
- Automated fixes go through the same review and CI as any other change.

## Security design

See [docs/security/index.md](docs/security/index.md) for the threat model, trust
boundaries and the runtime isolation plan.

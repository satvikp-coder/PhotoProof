# Security Policy

PhotoProof processes untrusted images and is intended to support forensic judgement,
so vulnerabilities can harm both users' systems and the integrity of conclusions
drawn from it.

## Supported versions

| Version | Supported |
|---|---|
| `0.1.x` (latest on `main`) | ✅ |
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

## Security design

See [docs/security/index.md](docs/security/index.md) for the threat model, trust
boundaries and the runtime isolation plan.

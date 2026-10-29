# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) as defined in CONTRIBUTING.md §6.

## [Unreleased]

### Added

- Support for Python 3.14 and 3.15 in the launcher, packaging and the CI matrix
  (3 OSes × Python 3.11–3.15). The launcher smoke test covers the oldest and newest.

### Changed

- Configuration parsing uses ruamel.yaml's safe, pure-Python loader instead of PyYAML,
  because PyYAML has no wheels for Python 3.15 and the launcher installs wheels only.
  Duplicate keys and Python tags are still rejected.
- The backlog separates hard dependencies (*blocked by*) from integration gates
  (*integrates with*), so 21 issues can start immediately instead of 9. Six enabling
  issues were added (synthetic test toolkit, shared isolation helper, copy-move
  contracts, plugin scaffold, team ownership, work-allocation guide), and the roadmap
  now lists work waves for a four-person team.
- Docs and security tooling moved to `requirements-tools.lock`. `requirements-dev.lock`
  now holds only tools that install on every supported Python.

## [0.1.0] - 2026-10-29

### Added

- Stdlib-only launcher `main.py` that validates the interpreter, OS, architecture and
  hardware, creates `.venv/`, installs hash-pinned dependencies only when the lockfile
  fingerprint changes, and supports `--doctor`, `--repair`, `--force-setup`,
  `--ignore-blacklist` and `--version` with documented exit codes.
- `app.toml` as the single source of the application identity and version. The wheel
  version is derived from it and CI verifies they match.
- Schema-validated `settings.yaml`, analysis profiles and per-plugin configuration, with
  safe YAML parsing, duplicate-key and unknown-key rejection, and production-only
  safety rules.
- Plugin policy with documented precedence (security denylist, authorized blacklist
  bypass, blacklist, feature flags, profile, metadata default) and recorded decisions.
- Plugin framework: trusted discovery, metadata validation, lazy loading, DAG planning
  that rejects cycles, missing dependencies, priority inversions and contract errors,
  deterministic priority scheduling, a typed execution state machine and a sequential
  executor with failure isolation, timeouts and abort/degrade policies.
- Run results that never imply authenticity: `COMPLETED`, `INCOMPLETE`, `ABORTED`,
  `BLOCKED` and `NO_DETECTORS`.
- SQLite persistence with transactional migrations and pre-migration backups.
- Runtime storage under `ARE/` with safe path handling, atomic writes and read-only,
  content-addressed blobs.
- Run-level result cache keyed on inputs, profile, effective configuration, plugin
  versions, implementation digests and pipeline fingerprint, with artifact
  verification and automatic invalidation.
- Deterministic execution manifests with a computational fingerprint, plus a
  `manifest` CLI command.
- Event bus with isolated optional subscribers and critical persistence and audit
  subscribers, structured JSON logging and an audit trail.
- Health checks with SAFE, CONTROLLED and MANUAL repair classes, dry-run support and
  post-repair verification.
- Decoder security interface (`ImageDecoder`, `DecodeLimits`) for future decoders.
- GitHub Actions for CI, security, docs, releases and PR governance; issue and PR
  templates; Dependabot; CODEOWNERS.
- MkDocs Material documentation and Read the Docs configuration.
- Test suite covering unit, integration, resilience, security and end-to-end
  behaviour, using mock plugins.

### Removed

- The Streamlit ELA and noise-residual prototype. It contradicted the plugin
  architecture, and ELA is now specified as a plugin in the backlog. The prototype is
  still available in commit `bf90881`.

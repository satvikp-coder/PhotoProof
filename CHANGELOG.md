# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) as defined in CONTRIBUTING.md §6.

## [Unreleased]

### Added

- `PillowDecoder` decodes JPEG, PNG and WebP with size limits checked before any pixel is
  allocated, and rejects truncated or corrupted files. Decoder versions are recorded in
  manifests and cache keys. The pipeline does not use it yet (#12).
- PhotoProof identifies image formats from file signatures. Formats outside
  `formats.accepted` are rejected before being stored, a plugin is `NOT_APPLICABLE` for formats
  outside its `supported_formats`, and `doctor` lists accepted formats with no decoder.
- `analyze` stores every input byte for byte in a read-only evidence store before any
  processing. Identical files are kept once with a record of each submission, and every read
  re-verifies the hash. Run `python main.py --repair` once to migrate an existing database
  (a backup is taken first).
- `doctor` reports an original that the database knows about but `objects/` no longer holds
  as a critical finding.
- Support for Python 3.14 and 3.15 in the launcher, packaging and the CI matrix
  (3 OSes × Python 3.11–3.15). The launcher smoke test covers the oldest and newest.
- The team page (`docs/planning/team.md`), which allocates every backlog issue to an
  owner. `CODEOWNERS` requests review from Satvik, Varun and Garv on every pull request.
- `AGENTS.md`, with instructions for coding agents and the code review rules used by
  automated pull request reviews.

### Changed

- `analyze` rejects symlinks and empty files, and it rejects an unknown profile or an invalid
  reference combination before storing anything.
- `SECURITY.md` adds review guidance (assets, trust boundaries, severity, known gaps) and
  lists `0.2.x` as the supported version.
- `app.toml`, `pyproject.toml`, `mkdocs.yml` and `LICENSE` name the four developers
  instead of placeholders.
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
- Preflight runs quick integrity checks, so startup time no longer grows with stored
  data. `doctor` and `repair` still re-hash every blob and cached artifact.
- The launcher no longer runs a separate preflight before every command. Commands that
  start the application run it themselves; `doctor`, `repair`, `config` and `plugins`
  skip it, so they no longer apply SAFE repairs as a side effect.
- Profile `required_plugins` now implicitly enable those plugins.
- Plugin implementation digests cover every file in the plugin package (subpackages
  and data files), not just top-level `*.py` files.
- Runs move through a `RUNNING` status in the database.

### Fixed

- `.readthedocs.yaml` quotes its install command, which YAML parsed as a mapping because of
  `:all:`, failing the Read the Docs build.
- `python main.py repair --dry-run` performed a real repair: the launcher consumed
  `--dry-run`. Launcher flags must now precede the command, and everything after it is
  forwarded unchanged.
- A run that failed unexpectedly (for example a critical subscriber error) stayed
  `CREATED` forever. It is now marked `ABORTED`, and a new `database.stale_runs` check
  reports runs that never finished.
- A malformed plugin configuration file aborted the whole analysis when the cache was
  enabled, but failed only that plugin with `--no-cache`. It now fails only its plugin
  in both cases.
- Plugins received shallow copies of their inputs and could mutate other plugins'
  recorded outputs.
- Cache hits recorded no plugin states or artifact references in the database.
- Health-check findings were never written to the `health_checks` table, and
  non-blocking warnings were never shown at startup.
- An unreadable (non-SQLite) database file crashed the CLI with a traceback.
- A failing `ROLLBACK` could mask the original database error.
- `plugins --profile <unknown>` crashed with a `KeyError`.
- Log file handlers were left open after shutdown (file locks on Windows).
- Atomic writes now also fsync the directory, so a completed rename survives power loss.

### Security

- Booleans and feature flags in configuration must be real booleans (`"yes"` or `1`
  are rejected).
- Plugin IDs in the blacklist, denylist and profiles must be well-formed. The new
  `plugins.policy` check reports entries naming unknown plugins, because a misspelled
  blacklist entry blocks nothing.
- `manifest` verifies the manifest file against the SHA-256 recorded at the end of the
  run, and refuses modified manifests.
- Event payloads are read-only, so one subscriber cannot alter what others see.
- Plugin outputs are capped at 1 MiB, artifact names must be unique per plugin run,
  and configuration files are capped at 1 MiB.
- Analysis requests validate the roles of the target and reference descriptors.
- A required plugin that needs a reference image now blocks a single-image profile at
  planning time instead of aborting mid-run.

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

# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) as defined in CONTRIBUTING.md §6.

## [Unreleased]

### Added

- A run resolves its media contracts from stored evidence, once per run, and hands them to
  plugins that declare them in `inputs`: `input.original_bytes` (the stored original),
  `image.decoded` (the pixels as decoded) and `image.working` (the normalized working
  image), the last two as read-only NumPy arrays. This is the groundwork of the plugin
  image-access contract (#12); decoder isolation is still #9. A run that cannot decode an
  image skips the declaring plugins with `dependency_unavailable`, records the reason in
  the manifest, and ends `INCOMPLETE` — missing pixels are never clean evidence.
- `core/database/store.py`: `Database.backup()` (durable SQLite backup via the online
  backup API) and `Database.verify_backup()` (read-only verification with `PRAGMA
  integrity_check`/`foreign_key_check`, row counts, and SHA-256 manifest) (#62).
- `cli.py`: New `python main.py backup` and `python main.py restore BACKUP [--dry-run]`
  commands for creating and restoring verified SQLite database backups (#62).
- `core/health/checks.py`: New `database.backup` health check that reports the age of the
  newest verified backup and warns when it exceeds `retention.backup_days` (#62).
- `core/config/validator.py`: `retention.backup_days` setting (`int | None`, default `None`)
  controls when the `database.backup` health check warns (#62).

### Fixed

- `core/pipeline/executor.py`: an `ArtifactRef` nested inside a summary output is now
  normalized to its JSON form, so a plugin that reports the artifact it wrote no longer
  fails `invalid_output`. A declared input the run could not provide skips the plugin with
  `dependency_unavailable` and the recorded reason instead of raising `KeyError`.
- `core/security/windows/acl.py`: `icacls` is resolved to its full path (Bandit B607), and
  the ACL reader now accepts the machine-qualified account and `OWNER RIGHTS`, and skips
  the directory path on the first output line, so owner-only runtime directories are no
  longer reported as open to other users (#80).
- `core/storage/evidence.py`: a staged file that lost the publish race is read-only, which
  Windows refuses to delete; cleanup now clears that mark first, so concurrent ingests of
  identical content leave no temporary files behind.
- `plugins/compression/ela/`: the processor returned an undeclared `ela_summary` output;
  it is now declared as `compression.ela_summary` and the plugin runs (#15).
- `plugins/noise/residual/` and `plugins/noise/variance_map/`: their summary outputs are
  declared (`noise.residual_summary`, `noise.variance_summary`) and a zero noise estimate
  no longer divides by zero in the Wiener threshold (#18, #19).
- `plugins/copymove/sift_keypoints/`: the processor read `image.decoded` while its metadata
  declared `image.working`; it now reads the declared working image, and keypoints are
  capped at `max_keypoints` because OpenCV's `nfeatures` is a hint (#20).
- `plugins/copymove/mask/` and `plugins/copymove/verification/`: `requires` listed input
  contracts instead of plugin ids, and verification asked for contracts nothing produces;
  both now name `copymove.matching`/`copymove.sift_keypoints` and the contracts their
  processors read (#23, #24).
- `plugins/metadata/exif/`: it declared `image.working` without reading it, which skipped
  metadata analysis whenever pixels could not be decoded; the declaration is gone (#14).
- `plugins/copymove/clustering/`: undefined variables, a missing NumPy import and
  incorrect return keys are fixed; `analyze_clustering` gained `method` and
  `min_matches_per_cluster`, and `clustering_result_to_dict` exists (#22).
- `core/isolation.py`: fixed mypy errors and the Unix-only `resource` import (#9).
- Tests: `tests.fixtures.*` imports work whether or not pytest is started with
  `python -m` (CI runs the console script), and mypy targets Linux, matching the CI
  quality job.

### Changed

- `core/isolation.py` runs a callable in a child process with wall-clock, CPU and memory limits
  and reports typed failures. `doctor` reports which limits this platform can enforce. Decoders
  and plugins do not use it yet (#9, #52).
- Pull requests automatically get the milestone of the issue they reference, unless someone has
  already set one.
- 16-bit PNGs decode without loss. High-depth images get a `uint16` view with their transfer
  function and primaries, and an 8-bit derivative through a documented tone curve. Plugins
  that declare `image.decoded` receive the 16-bit view (#12).
- `normalize` turns a decoded image into a working image in sRGB with EXIF orientation applied,
  and records every transform. The decoded image stays available and unchanged. Plugins that
  declare `image.working` now receive it (#12).
- `IngestionService` stages each input in quarantine, enforces the size limit while reading,
  identifies its type from content, and audits every rejection as `INPUT_REJECTED`. `analyze` now
  uses it for all inputs.
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
- `imaging/arrays.py` defines the shared NumPy image-array contract for detector
  plugins (`validate_image`, `to_float32`, `to_luma`, read-only enforcement). The
  `synthetic` pytest fixture and `tests/fixtures/synthetic.py` provide seeded
  synthetic forgery generators with exact ground-truth masks (#72). NumPy and
  Pillow are now hash-pinned runtime dependencies.
- `imaging/stats.py` provides shared block tiling (`tile`), block reduction
  (`block_reduce`), robust z-scores (`robust_zscore`), `AnomalyMap` and block-map
  upsampling (`upsample`) for regional detectors (#13). All detectors use the same
  statistical language.
- `plugins/metadata/exif/` implements the EXIF metadata forensic plugin
  (`metadata.exif`) that extracts EXIF/XMP data and reports typed provenance
  findings (editor detection, impossible dates, dimension mismatches, thumbnail
  inconsistencies, absent metadata). GPS coordinates are never emitted (#14).
- `plugins/compression/ela/` implements Error Level Analysis as a supporting
  signal (#15). Recompresses at controlled JPEG quality, computes per-pixel
  error, builds an `AnomalyMap` via block reduction and robust z-scores.
  Explicitly labelled supporting evidence only.
- `plugins/compression/jpeg_dct/` implements JPEG DCT/quantization analysis
  (#16). Parses quantization tables from DQT markers, estimates JPEG quality,
  and builds a per-block double-compression likelihood map via DCT coefficient
  histogram periodicity analysis. Pure-Python bitstream parsing.
- `plugins/compression/jpeg_grid/` implements JPEG 8x8 grid misalignment
  detection (#17). Estimates the dominant blocking-grid origin via Blocking
  Artifact Grid analysis and produces a per-window mismatch map.

- `plugins/noise/residual/` implements noise residual extraction (#18).
  Recompresses at controlled JPEG quality, computes per-pixel absolute error,
  builds an `AnomalyMap` via block reduction and robust z-scores.
  Explicitly labelled supporting evidence only.

- `plugins/noise/variance_map/` implements noise variance estimation and
  consistency mapping (#19). Estimates local noise sigma per block from the
  residual, applies signal-dependent correction, and builds a robust-z
  consistency map. Provides coverage fraction and confidence level.

- `plugins/copymove/sift_keypoints/` implements SIFT keypoint extraction (#20).
  Detects SIFT keypoints and computes descriptors on the decoded image, the first
  stage of the copy-move detection chain.

- `plugins/copymove/matching/` implements SIFT descriptor matching (#21).
  Matches descriptors using KNN and generalized Lowe ratio filtering (g2NN),
  with spatial distance constraints and symmetric deduplication.

- plugins/copymove/clustering/` implements copy-move clustering (#22).
  Groups matched keypoint pairs into clusters using hierarchical clustering
  (Ward linkage) or DBSCAN on displacement vectors. Produces per-cluster
  bounding boxes, displacement vectors, and sizes.

- `plugins/copymove/mask/` implements copy-move region masks (#24).
  Generates pixel-level source/target localization masks from verified
  copy-move matches using bounded ZNCC correlation.

- `plugins/visual/sharpness/` implements local sharpness analysis (#25).
  Maps local focus using the variance of the Laplacian, with optional
  contrast normalization and robust-z inconsistency detection. A weak
  visual cue for copy-move detection.

- `plugins/visual/edges/` implements edge boundary analysis (#26).
  Characterizes object boundaries using Canny edge detection and Sobel
  gradient analysis. Estimates edge width and flags boundaries inconsistent
  with the image's typical edge-width distribution. Complementary to #25.

### Changed

- The version now changes only at release time, in a `chore(release)` PR. Ordinary PRs leave
  `app.toml` alone, so open PRs can't collide on the version, and `main` moving on can't fail them.
- `analyze` rejects files that are not a recognised image format.
- The pull request template no longer has checkboxes that cannot all be ticked, so a complete
  PR shows every task as done. `CONTRIBUTING.md` explains how to fill it in and how to link an
  issue so that it closes on merge.
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

- Merging two pull requests close together no longer cancels the first one's CI on `main`, which
  left a failed-looking mark on that commit.
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

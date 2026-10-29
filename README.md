# PhotoProof

[![CI](https://github.com/satvikp-coder/PhotoProof/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/satvikp-coder/PhotoProof/actions/workflows/ci.yml)
[![Security](https://github.com/satvikp-coder/PhotoProof/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/satvikp-coder/PhotoProof/actions/workflows/security.yml)
[![Docs](https://github.com/satvikp-coder/PhotoProof/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/satvikp-coder/PhotoProof/actions/workflows/docs.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776AB)
[![License](https://img.shields.io/github/license/satvikp-coder/PhotoProof)](LICENSE)
[![Release](https://img.shields.io/github/v/release/satvikp-coder/PhotoProof?include_prereleases&sort=semver)](https://github.com/satvikp-coder/PhotoProof/releases)

PhotoProof is a modular framework for **digital image forgery detection and
localization**. Detectors are independent plugins that a dependency-aware pipeline
plans, runs, records and caches reproducibly.

> **Status: 0.1.0, foundation only.** The architecture is implemented and tested.
> **No forensic detector, image decoder or web interface exists yet.** An analysis
> reports `NO_DETECTORS` and never produces an authenticity verdict. The remaining
> work is tracked as [GitHub issues](https://github.com/satvikp-coder/PhotoProof/issues)
> and summarized in [docs/planning](docs/planning/index.md).

## What exists today

- **Stdlib-only launcher** (`main.py`): checks the interpreter, OS, architecture,
  CPU, memory and disk; creates `.venv/`; installs **hash-pinned** dependencies only
  when the lockfile fingerprint changes; runs preflight health checks.
- **Validated configuration:** `app.toml` (identity, single version source),
  `settings.yaml`, analysis profiles and per-plugin configs, all schema-checked with
  strict unknown-key rejection.
- **Plugin policy:** security denylist › authorized `--ignore-blacklist` › blacklist ›
  feature flags › profile › metadata default, with every decision recorded.
- **Plugin framework:** trusted discovery, metadata validation, lazy loading, DAG
  planner (cycles, missing dependencies, priority inversions and contract errors are
  rejected), deterministic `(priority, discovery order)` scheduling, typed state
  machine, sequential executor with failure isolation, timeouts and abort/degrade
  policies.
- **Persistence and reproducibility:** SQLite with migrations, content-addressed
  read-only artifacts, a run-level cache keyed on every result-affecting identity, and
  deterministic execution manifests.
- **Operations:** event bus, structured JSON logs, an audit trail, and `--doctor` /
  `--repair` with SAFE, CONTROLLED and MANUAL repair classes.
- **Automation:** CI on 3 OSes × 3 Python versions, security scanning (pip-audit,
  Bandit, CodeQL, TruffleHog), strict docs builds, PR governance and tag-driven
  releases.

## Quick start

Requires an installed **Python 3.11–3.13**. The launcher does not install Python.

```bash
git clone git@github.com:satvikp-coder/PhotoProof.git
cd PhotoProof
python3.12 main.py                   # set up the environment and show status
python3.12 main.py --doctor          # diagnose; changes nothing
python3.12 main.py plugins           # registered plugins and effective policy
python3.12 main.py analyze photo.jpg # runs the pipeline (currently: NO_DETECTORS)
python3.12 main.py manifest <run-id> # inspect the execution manifest
```

| Launcher flag | Purpose |
|---|---|
| `--version` | Print the version from `app.toml` |
| `--doctor` | Environment and application diagnostics |
| `--repair [--dry-run]` | Controlled repair (may rebuild `.venv/`) |
| `--force-setup` | Reinstall locked dependencies |
| `--ignore-blacklist` | Audited, local-only bypass of `plugins.blacklist` |

## Repository layout

```text
main.py            launcher            app.toml        identity and version
settings.yaml      global settings     configs/        profiles and plugin parameters
src/photoproof/    application         tests/          unit … e2e, mock plugins
docs/              MkDocs site         WebKit/         web UI (planned)
ARE/               runtime data (generated, Git-ignored)
```

## Documentation

The docs are built with MkDocs Material (`mkdocs serve`). Read the Docs hosting is
configured (`.readthedocs.yaml`) but not yet connected. Key pages:

- [Architecture](docs/architecture/index.md) and [execution lifecycle](docs/architecture/lifecycle.md)
- [Plugin development](docs/plugins/index.md)
- [Configuration](docs/configuration/index.md) and [settings reference](docs/configuration/settings.md)
- [Operations](docs/operations/index.md) and [security model](docs/security/index.md)

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). In short: issue → branch → tests → docs →
changelog → version bump → PR → review → squash merge. Report vulnerabilities
privately as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)

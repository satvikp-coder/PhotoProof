# Repository structure

```text
PhotoProof/
├── main.py                 stdlib-only launcher
├── AGENTS.md               instructions and review rules for coding agents
├── app.toml                identity and version (single source of truth)
├── settings.yaml           global behaviour
├── pyproject.toml          packaging (version derived from app.toml), tool config
├── requirements.lock       hash-pinned runtime + build-backend dependencies
├── requirements-dev.lock   hash-pinned test/lint/type tooling (all Pythons)
├── requirements-tools.lock hash-pinned docs and security tooling (Python ≤ 3.13)
├── configs/profiles/       analysis profiles (standard, comparison, research)
├── configs/plugins/        optional per-plugin parameters (<plugin_id>.yaml)
├── src/photoproof/
│   ├── cli.py              command-line interface
│   ├── contracts/          stable shared types (no core imports)
│   ├── versioning/         SemVer, compatibility, build info
│   ├── core/               bootstrap, controller and services
│   ├── imaging/            format detection, decoder interface and adapters
│   └── plugins/            trusted built-in detectors (none yet)
├── WebKit/                 server-rendered web foundation
├── models/                 pretrained model weights (planned; Git-ignored)
├── media/                  repository-managed images via Git LFS
├── ARE/                    Application Runtime Environment (generated, Git-ignored)
├── tests/                  unit, integration, resilience, security, e2e, fixtures
├── docs/                   this site
└── .github/                workflows, templates, governance script
```

Modules named in the original architecture but not yet needed (job queue, workers,
housekeeping service, reporting, image registration/comparison) are intentionally
absent; each has a backlog issue. Empty placeholder modules would misrepresent what
is implemented.

## Static bootstrap paths

These are fixed because they are needed before configuration loads:

| Path | Used by |
|---|---|
| `app.toml`, `requirements.lock`, `pyproject.toml`, `.venv/` | `main.py` |
| `settings.yaml`, `configs/profiles/`, `configs/plugins/` | `core/config/loader.py` |
| Repository root = three levels above `core/bootstrap.py` | `core/bootstrap.py` (editable install from a checkout) |

Everything else — notably the runtime root `ARE/` — comes from `settings.yaml`.

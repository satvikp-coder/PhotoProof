# Development

The full contributor handbook — setup, coding standards, branches, commits, PRs and
the Definition of Done — is
[CONTRIBUTING.md](https://github.com/satvikp-coder/PhotoProof/blob/main/CONTRIBUTING.md).
This page is the quick reference.

## Installation

```bash
python3.12 main.py                                         # runtime environment
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
source .venv/bin/activate
```

## Everyday commands

| Task | Command |
|---|---|
| Format | `ruff format .` |
| Lint | `ruff check .` |
| Types | `mypy` (strict, `src/`) |
| Tests | `pytest` |
| Coverage | `pytest --cov=photoproof --cov-report=term-missing` |
| Security lint | `bandit -c pyproject.toml -r src main.py` |
| Dependency audit | `pip-audit --require-hashes --disable-pip -r requirements.lock` |
| Docs preview | `mkdocs serve` |
| Docs build (CI) | `mkdocs build --strict` |

## Workflow

`ISSUE → BRANCH → IMPLEMENTATION → TESTS → DOCUMENTATION → CHANGELOG → VERSION → PR → CI → REVIEW → MERGE`

Branches: `feat/42-image-decoder`. Commits and PR titles: Conventional Commits.
Every merged PR bumps `app.toml` once and adds a `CHANGELOG.md` entry.

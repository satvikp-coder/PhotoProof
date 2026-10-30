# Contributing to PhotoProof

This handbook is the working agreement for everyone who changes PhotoProof. It covers
how to set up a development environment, the standards code must meet, and the exact
path a change takes from an issue to `main`.

Keep it accurate. If a rule here no longer matches the repository, open a
`docs/…` pull request that fixes the handbook rather than ignoring it.

---

## 1. Development setup

### 1.1 Prerequisites

| Requirement | Notes |
|---|---|
| Python **3.11 – 3.15** | Declared in `app.toml` (`[python]`). The launcher refuses other versions. |
| Git ≥ 2.30 and Git LFS | LFS is used for binary files under `media/`. Run `git lfs install` once. |
| Linux, macOS or Windows | x86_64 or arm64. See `app.toml` `[platform]`. |
| ~1 GB free disk | Virtual environment plus runtime data. |
| [`uv`](https://docs.astral.sh/uv/) (optional) | Only needed to **regenerate** lockfiles. Not needed to run or test. |

The launcher is written against the standard library only, but it still needs an
existing compatible Python interpreter to start. It never installs Python for you.

### 1.2 First run

```bash
git clone git@github.com:satvikp-coder/PhotoProof.git
cd PhotoProof
python3.12 main.py            # any supported interpreter
```

`main.py` creates `.venv/`, installs the hash-pinned runtime dependencies from
`requirements.lock`, installs PhotoProof in editable mode, prepares `ARE/`, runs
preflight health checks and starts the application shell. Later launches skip
installation unless the lockfile, `pyproject.toml`, the application version or the
interpreter changed.

Useful launcher modes:

| Command | Purpose |
|---|---|
| `python main.py --version` | Print the version from `app.toml` and exit. |
| `python main.py --doctor` | Diagnose environment and application health; changes nothing. |
| `python main.py --repair --dry-run` | Show what a repair would do. |
| `python main.py --repair` | Apply safe and controlled repairs (may rebuild `.venv/`). |
| `python main.py --force-setup` | Reinstall locked dependencies even if nothing changed. |
| `python main.py <command> …` | Forward a command to the CLI, e.g. `python main.py plugins`. |

### 1.3 Development dependencies

Development tools live in two more hash-pinned lockfiles:

- `requirements-dev.lock`: pytest, Ruff, mypy, build. Installs on every supported Python.
- `requirements-tools.lock`: the dev tools plus MkDocs, Bandit and pip-audit. These
  depend on PyYAML and watchdog, which do not yet publish wheels for the newest
  Pythons, so use Python 3.11–3.13 for docs and security tooling (CI uses 3.12).

```bash
# macOS / Linux
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .

# Windows
.venv\Scripts\python -m pip install --require-hashes -r requirements-dev.lock
.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
```

Activate the environment (`source .venv/bin/activate` or `.venv\Scripts\activate`)
for the commands below.

Optionally install the Git hooks: `pre-commit install --hook-type pre-commit --hook-type commit-msg`
(`pre-commit` is not in the lockfile; install it with `pipx`).

### 1.4 Formatting and linting

```bash
ruff format .          # format
ruff check .           # lint
ruff check . --fix     # apply safe autofixes
```

CI runs `ruff format --check .` and `ruff check .`. Both must pass.

### 1.5 Type checking

```bash
mypy
```

`mypy` is configured in `pyproject.toml` and runs in strict mode over `src/`.
Public functions and methods must be fully annotated.

### 1.6 Tests

```bash
pytest                                  # everything
pytest tests/unit                       # fast, isolated
pytest tests/integration                # bootstrap + full orchestration with mock plugins
pytest tests/resilience tests/security  # fault injection and policy enforcement
pytest tests/e2e                        # real subprocess invocations of main.py and the CLI
pytest --cov=photoproof --cov-report=term-missing
```

Tests must never touch your real `.venv/`, `ARE/` or home directory. Use the `tmp_path`
fixture and the helpers in `tests/conftest.py` (`app_root`, `make_app`).

### 1.7 Documentation preview

```bash
mkdocs serve           # http://127.0.0.1:8000, live reload
mkdocs build --strict  # what CI runs; warnings are errors
```

### 1.8 Regenerating lockfiles

Only do this in a pull request whose purpose is dependency changes.

```bash
uv pip compile pyproject.toml --group build --universal --python-version 3.11 \
    --generate-hashes -o requirements.lock
uv pip compile pyproject.toml --group build --group dev --universal --python-version 3.11 \
    --generate-hashes -o requirements-dev.lock
uv pip compile pyproject.toml --group build --group dev --group docs --group security \
    --universal --python-version 3.11 --generate-hashes -o requirements-tools.lock
```

All three files are universal (one file for every supported OS and Python version) and
every requirement carries `--hash` values. Never hand-edit them.

---

## 2. Coding standards

### 2.1 General

- Follow **PEP 8**; Ruff enforces the details. Line length is 100.
- **Type hints on every public interface.** Prefer precise types over `Any`.
- **Clear signatures.** Keyword-only arguments (`*`) for anything that is not obvious
  from position. No boolean positional "mode" parameters.
- **Descriptive names.** `plugin_id`, not `pid`; `artifact_sha256`, not `h`.
- **Small, cohesive modules.** One responsibility per module. If a module needs a
  paragraph to explain what it does, split it.
- **Document public contracts** with docstrings: what it does, what it raises, and any
  invariant callers rely on. Do not restate the code.
- **Respect the dependency direction:** `CLI / WebKit → core → contracts`. `contracts`
  imports nothing from `core`. Plugins import only from `photoproof.contracts`.
- **No circular imports.** If two modules need each other, the shared type belongs in
  `contracts`.
- **No unexplained global state.** Services are constructed in `core/bootstrap.py`
  and passed explicitly.

### 2.2 Errors

- Raise specific exceptions from `photoproof.contracts.exceptions`.
- **No bare `except:`** and no `except Exception: pass`. If you catch broadly, you must
  log or record the failure and say why the broad catch is correct (plugin isolation,
  optional subscriber isolation).
- **No silent failure swallowing.** A missing result is never "probably fine".
- Error messages must be actionable: say what was wrong, where, and what to do.

### 2.3 Forensic integrity

- A missing or failed detector result is **never** evidence of authenticity.
- Never present a heuristic score as a calibrated probability.
- Original evidence is immutable. Code that writes to original bytes will be rejected.

### 2.4 Repository hygiene

- **No unrelated changes in a pull request.** Drive-by refactors go in their own PR.
- **No secrets.** Use `.env` (ignored by Git); document new variables in `.env.example`.
- **No user-uploaded photographs or datasets in Git.** Only images you have the right
  to redistribute may go under `media/`, through Git LFS.
- **No unnecessary dependencies.** A new dependency needs a justification in the PR:
  what it replaces, its licence, and its maintenance status. The standard library and
  existing dependencies come first.

### 2.5 Tooling

| Concern | Tool |
|---|---|
| Lint and format | Ruff |
| Types | mypy (strict) |
| Tests | pytest, pytest-cov |
| Static security | Bandit |
| Dependency audit | pip-audit |
| Docs | MkDocs + Material for MkDocs |

---

## 3. Branches

Never commit feature work directly to `main`. Create a branch from up-to-date `main`:

```
feat/short-description
fix/short-description
docs/short-description
refactor/short-description
test/short-description
chore/short-description
security/short-description
```

Include the issue number whenever there is one: `feat/42-image-decoder`.
Use lowercase words separated by hyphens.

---

## 4. Commits — Conventional Commits 1.0.0

```
type(scope): concise imperative description

Optional body explaining what and why (not how).

Optional footers, e.g. Refs: #42
```

| Type | Use for |
|---|---|
| `feat` | New user-visible capability |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change with no behaviour change |
| `perf` | Performance improvement |
| `test` | Tests only |
| `build` | Packaging, dependencies, lockfiles |
| `ci` | GitHub Actions and automation |
| `chore` | Maintenance that fits nothing else |
| `style` | Formatting only |
| `revert` | Reverting a previous commit |

Scopes match the area you touched: `pipeline`, `config`, `cache`, `storage`,
`database`, `health`, `events`, `launcher`, `cli`, `plugins`, `imaging`, `security`,
`WebKit`, `docs`, `workflows`, `deps`, …

Good:

```
feat(pipeline): add dependency-aware scheduling
fix(cache): invalidate stale plugin results
docs(architecture): document execution state machine
test(registry): cover duplicate plugin identifiers
ci(workflows): add Windows compatibility tests
refactor(config): centralize settings validation
chore(deps): update development dependencies
```

Breaking changes use `!` **and** a footer:

```
feat(plugins)!: replace plugin execution contract

BREAKING CHANGE: plugins must return a PluginOutput instead of a mapping.
Migrate by wrapping existing return values in PluginOutput(values=...).
```

Not acceptable: `update`, `changes`, `fixed stuff`, `final`, `misc`, `working`,
`new code`. One logical change per commit. The `commit-msg` pre-commit hook and the
PR-governance workflow check the format.

---

## 5. Issue-driven development

Every change follows this path:

```
ISSUE → BRANCH → IMPLEMENTATION → TESTS → DOCUMENTATION → CHANGELOG → VERSION → PR → CI → REVIEW → MERGE
```

1. **Find or open an issue.** Use the issue templates. Large or unclear work gets
   `status:needs-design` until the approach is agreed. Pick `status:ready` issues.
   *Blocked by* means you cannot start yet. *Integrates with* means you can start now
   against the documented contract, and finish the integration when the other issue
   lands (detectors: `algorithm.py` PR first, `processor.py` wiring PR second).
2. **Discuss scope** in the issue when the acceptance criteria are ambiguous.
3. **Assign yourself** (or ask a maintainer) and set `status:in-progress`.
4. **Branch** using the naming rules above.
5. **Implement** exactly the acceptance criteria. Out-of-scope ideas become new issues.
6. **Test**: add tests that would fail without your change.
7. **Document**: update the relevant pages under `docs/`. Never describe planned
   behaviour as implemented.
8. **Changelog**: add entries under `## [Unreleased]` in `CHANGELOG.md`.
9. **Version**: bump `version` in `app.toml` (section 6).
10. **Open a PR** that references the issue (`Closes #42`) and completes the template.
11. **Wait for CI.** Fix failures; do not ask reviewers to look at red builds.
12. **Request review** from at least one other developer.
13. **Address feedback** with new commits (no force-push after review starts unless
    asked).
14. **Merge** only when every required check passes and review is approved.

---

## 6. Versioning

PhotoProof uses **Semantic Versioning** (`MAJOR.MINOR.PATCH`). The only place the
version is written is `app.toml` → `[application].version`. The package version is
derived from it at build time; CI fails if anything disagrees.

**Every merged PR bumps the version once**, using the highest applicable category:
`MAJOR > MINOR > PATCH`. Multiple commits in one PR share one bump.

| Bump | When | Example |
|---|---|---|
| **PATCH** | Bug fixes, security fixes without interface change, docs, tests, internal refactors, CI fixes, compatible optimizations, logging improvements | `0.1.0 → 0.1.1` |
| **MINOR** | New feature, forensic algorithm, optional plugin capability, image format; compatible behaviour changes; removing explicitly experimental features | `0.1.4 → 0.2.0` (PATCH resets) |
| **MAJOR** | Breaking a supported contract: public interface, plugin API, CLI, configuration format, manifest/artifact format, removing supported functionality | `0.9.2 → 1.0.0` |

- Lines changed are irrelevant; compatibility impact decides.
- The same strict policy applies during `0.x`. A breaking change is not a MINOR just
  because we are pre-1.0. It needs explicit maintainer approval and migration notes.
- Plugin versions (`METADATA["version"]`) are independent of the application version
  and follow the same rules for the plugin's own contract.
- Changing the plugin execution contract also requires bumping
  `plugin_api_version` in `app.toml`.

---

## 7. Changelog

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Add entries under `## [Unreleased]` in the right section: **Added, Changed,
Deprecated, Removed, Fixed, Security**.

Write entries that a user of the project can understand:

- ✅ `Planner rejects plugins whose required dependency has a later priority, preventing silent reordering.`
- ❌ `Fixed planner.`

At release time the maintainer renames `[Unreleased]` to `[x.y.z] - YYYY-MM-DD`.

---

## 8. Pull requests

Complete every section of the PR template:

- Summary, motivation and related issue (`Closes #…`)
- Scope of changes and implementation details
- Test evidence (commands and results)
- Documentation changes
- Version impact and changelog entry
- Compatibility and security considerations
- Screenshots or execution examples where relevant

Filling it in:

- **Version impact and documentation changes are written as text.** State `PATCH`, `MINOR` or
  `MAJOR`, and list the pages you updated, or say why none needed changing.
- **Tick every box in the final checklist.** GitHub counts each `- [ ]` line in the description
  as a task, so a complete PR shows all of them done. Don't add unticked boxes for alternatives.
- **Link the issue with `Closes #…`.** Only closing keywords (`Closes`, `Fixes`, `Resolves`)
  link a PR to its issue and close it on merge. `Refs #…` does neither, so use it only for
  partial work, such as the first of a detector's two PRs. If a criterion moves to another
  issue, say so in both places.

Rules:

- At least **one approving review from another developer**.
- **No merging with failing required checks.**
- **Squash merge.** The squash commit title must be a valid Conventional Commit (the PR
  title is used, which is why the governance workflow checks it).
- Keep PRs small enough to review in one sitting. Several hundred lines of logic is a
  signal to split.
- Automated review follows the rules in [AGENTS.md](AGENTS.md#code-review-rules) and
  [SECURITY.md](SECURITY.md#security-review-guidance). It supports the required human
  approval and does not replace it.

The **PR governance** workflow checks the title, issue reference, version bump and
changelog entry and reports documentation and test changes. Its messages say how to
fix each failure. In the rare case a check does not apply (for example, reverting a
release), a maintainer may add the `governance:exempt` label with a comment explaining
why.

---

## 9. Definition of Done

An issue is done only when **all** of the following hold:

- [ ] Acceptance criteria are satisfied.
- [ ] Relevant automated tests exist and pass.
- [ ] Ruff, mypy and Bandit pass.
- [ ] Documentation is updated and `mkdocs build --strict` passes.
- [ ] `app.toml` version is bumped correctly.
- [ ] `CHANGELOG.md` is updated.
- [ ] Security considerations are addressed or explicitly noted as not applicable.
- [ ] Required review is completed.
- [ ] The change is merged through a pull request.

Do not close an issue because code was written. Close it when the PR that satisfies it
is merged.

---

## 10. Security

Do **not** open public issues for vulnerabilities. Follow [SECURITY.md](SECURITY.md).

## 11. Code of conduct

Be respectful, specific and constructive in reviews. Critique the code, not the
person. Maintainers may moderate discussions that violate this.

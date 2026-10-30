# CI/CD and repository settings

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | PR to `main`, push to `main`, manual | Quality (Ruff lint and format, mypy strict, config and plugin-graph validation, version consistency); tests on Ubuntu, Windows and macOS × Python 3.11–3.15 with JUnit and coverage; package build with version check; launcher smoke test per OS on the oldest and newest Python (first launch, no-reinstall second launch, doctor, repair dry run); summary job with result and matrix tables |
| `security.yml` | PR, push, weekly, manual | Lockfile pin/hash integrity and pyproject↔lock consistency, pip-audit, Bandit, security policy tests, TruffleHog secret scan, CodeQL (`security-extended`) |
| `docs.yml` | PR, push, manual | Backlog source validation, `mkdocs build --strict` (broken links, nav and anchors fail), site artifact |
| `release.yml` | Tag `vX.Y.Z` pushed by a maintainer | Validate tag, version and changelog; refuse duplicates; test; build; create GitHub release |
| `pr-governance.yml` | PR events | Conventional title, issue reference, exactly-one version bump, changelog entry; warnings for missing docs/tests |

All actions are pinned to full commit SHAs (with the tag in a comment). Workflows
default to `permissions: contents: read`. `pull_request_target` is not used, and PR
text reaches scripts through environment variables, never through shell
interpolation.

## Branch protection (manual — requires admin)

The foundation was set up with write (not admin) access, so a repository admin must
configure **Settings → Rules → Rulesets → New branch ruleset** for `main`:

1. Target: default branch.
2. Enable **Restrict deletions** and **Block force pushes**.
3. Enable **Require a pull request before merging**, with 1 required approval,
   *Dismiss stale approvals*, and *Require conversation resolution*.
4. Enable **Require status checks to pass**, with *Require branches to be up to date*,
   and add: `Quality · lint, format, types, config`, `Summary`, `Package build`,
   `MkDocs strict build`, `Title · issue · version · changelog`,
   `Dependency audit and lockfile integrity`, `CodeQL`.
5. Optional: **Require linear history**. Allow only **squash merging** under
   Settings → General → Pull Requests.
6. Bypass list: repository admins only, for emergencies.

## Automated PR review (manual)

Codex reviews pull requests using the `## Code Review Rules` section of `AGENTS.md`. It
needs push or admin permission on the repository, and it is enabled in the Codex settings
rather than in this repository:

1. Connect the repository to Codex.
2. Under **Review code**, turn on **Automatic review** for the repository.
3. Under **Personal preferences**, turn on **Automatic review** and choose a **Review
   trigger**.
4. Optional: under **Review security vulnerabilities**, turn on **Auto security review**
   and set **Trigger** to **Whenever code review runs**. It is a research preview.

To request a review by hand, comment `@codex review` or `@codex security review` on the
PR. Reviews flag only P0 and P1 issues. They do not replace tests, branch protection or
the required approval.

## Read the Docs (manual)

1. Sign in at readthedocs.org with GitHub and **Import a Project** →
   `satvikp-coder/PhotoProof`.
2. It builds with `.readthedocs.yaml` (Python 3.12, locked dev requirements,
   `fail_on_warning: true`).
3. Enable *Build pull requests* under Settings → Pull request builds.
4. Then add the docs badge to the README.

The documentation is not published anywhere until this is done.

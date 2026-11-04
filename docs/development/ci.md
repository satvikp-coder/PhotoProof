# CI/CD and repository settings

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | PR to `main`, push to `main`, manual | Quality (Ruff lint and format, mypy strict, config and plugin-graph validation, version consistency); tests on Ubuntu, Windows and macOS × Python 3.11–3.15 with JUnit and coverage; package build with version check; launcher smoke test per OS on the oldest and newest Python (first launch, no-reinstall second launch, doctor, repair dry run); summary job with result and matrix tables |
| `security.yml` | PR, push, weekly, manual | Lockfile pin/hash integrity and pyproject↔lock consistency, pip-audit, Bandit, security policy tests, TruffleHog secret scan, CodeQL (`security-extended`) |
| `docs.yml` | PR, push, manual | Backlog source validation, `mkdocs build --strict` (broken links, nav and anchors fail), site artifact |
| `release.yml` | Tag `vX.Y.Z` pushed by a maintainer | Validate tag, version and changelog; refuse duplicates; test; build; create GitHub release |
| `pr-milestone.yml` | PR opened, edited, reopened or marked ready | Copies the milestone from the issue the PR references (`Closes`, `Refs` and so on). A milestone set by hand is never overwritten, and if linked issues disagree the earliest wins. PRs from forks are skipped, because their token cannot write |
| `pr-governance.yml` | PR events | Conventional title, issue reference, changelog entry; warnings for missing docs/tests |

All actions are pinned to full commit SHAs (with the tag in a comment). Workflows
default to `permissions: contents: read`, and `pr-milestone.yml` adds `pull-requests: write`
for its one job. `pull_request_target` is not used, and PR
text reaches scripts through environment variables, never through shell
interpolation.

## Branch protection

Read back through the administrator GitHub API on **2026-10-31**. The active ruleset is
[main relaxed protection](https://github.com/satvikp-coder/PhotoProof/rules/26237915),
targeting `refs/heads/main`:

- Pull requests require one approving review; only squash merging is allowed.
- Force pushes and branch deletion are blocked for actors subject to the rules.
- Required GitHub Actions checks: `Quality · lint, format, types, config`, `Summary`,
  `Package build`, `MkDocs strict build`, `Title · issue · version · changelog`,
  `Dependency audit and lockfile integrity`, and `CodeQL`. `Summary` covers the test
  and launcher matrices; these are actual workflow job names.
- **Stale approvals are not dismissed, conversation resolution is not required, and
  branches need not be up to date.**
- Bypass actors are repository role **Write** (ID 4), pull-request-only bypass,
  and **Admin** (ID 5), always bypass. The authenticated administrator reports
  `current_user_can_bypass: always`; protection must not be described as universal.

These are observed settings, not the stricter policy originally requested by issue 63.
Conversation/stale-approval enforcement and admins-only bypass remain a policy gap;
issue 63 must remain open until maintainers explicitly reconcile that requirement.
Historical experiments do not demonstrate enforcement of today's relaxed policy.
One-time evidence and pending acceptance work belong in the PR, not this reference.

Inspect the live state before any administrator change:

```bash
gh api repos/satvikp-coder/PhotoProof/rulesets/26237915
gh api repos/satvikp-coder/PhotoProof/rules/branches/main
```

Settings → Rules → Rulesets is authoritative. No checked-in payload is maintained:
reapplying an old snapshot would silently change policy. Update this dated summary
when the live settings change. Never test a direct write against main.

### Merge settings and private reporting

Verified on **2026-10-31**: squash merges enabled, merge/rebase methods disabled,
`squash_merge_commit_title=PR_TITLE`, `squash_merge_commit_message=PR_BODY`.
Private vulnerability reporting returns `enabled: true`; use the private route in
[SECURITY.md](https://github.com/satvikp-coder/PhotoProof/blob/main/SECURITY.md).

### Dependabot governance

Dependabot has no automatic governance exemption: titles must follow Conventional
Commits. A maintainer may add `governance:exempt` with an explanation for applicable
issue/changelog checks; the title check still runs. That label changes no ruleset
approval/status requirements. Dependency maintenance is tracked separately in issue 78.

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

## Read the Docs

Verified on **2026-10-31**: the connected project is `photoproof`, for this
repository, with `main` as the default branch and `latest` as its published version.
The canonical site is [photoproof.readthedocs.io](https://photoproof.readthedocs.io/).
GitHub PRs receive the `docs/readthedocs.org:photoproof` preview status; PR builds are
enabled. The README badge uses this confirmed project, not a guessed URL. Public
build evidence is recorded in the PR; no duplicate RTD project was imported.

For administrators reconnecting or reproducing the integration:

1. Sign in at readthedocs.org with GitHub and **Import a Project** →
   `satvikp-coder/PhotoProof`.
2. Select `main` as the default branch and `.readthedocs.yaml` as the configuration
   file. Connect the GitHub App/integration and verify delivery of push and PR events.
3. The checked-in v2 configuration uses Ubuntu 24.04, Python 3.12 and
   `requirements-tools.lock`, installed with hashes and wheels only. It points to
   `mkdocs.yml` with `fail_on_warning: true`. MkDocs uses Material and validates
   navigation, relative links and anchors. No application installation is needed.
4. Under Settings → Pull request builds, confirm PR builds are enabled (new projects
   enable previews by default). Open or update a PR and verify its RTD check and
   preview URL. Keep secrets out of public build environment variables.
5. Trigger a successful build of `main`. Open **View Docs** from that build and
   confirm the published site belongs to this repository. Record the actual project
   slug, canonical documentation URL, build URL and verification date here.
6. Only then copy that project's badge from its dashboard into README.md and update
   the README hosting status. Do not infer the slug from the repository name.

GitHub's repository hook list was empty on **2026-10-30**; that did not indicate an
absent GitHub App integration. Public RTD evidence above supersedes the initial
unverified hosting status. Issue #63 still requires the remaining protection
acceptance scenarios and normal human review before it can be completed.
The existing YAML matches the [RTD configuration reference](https://docs.readthedocs.com/platform/stable/config-file/v2.html);
preview setup follows [RTD pull request previews](https://docs.readthedocs.com/platform/stable/pull-requests.html).


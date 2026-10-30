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

## Branch protection

Applied and verified through the GitHub API on **2026-10-30**:
[main repository protection](https://github.com/satvikp-coder/PhotoProof/rules/26226021)
is active. The effective rules endpoint for `main` returns the rules below. The
reproducible creation payload is [`.github/main-ruleset.json`](https://github.com/satvikp-coder/PhotoProof/blob/main/.github/main-ruleset.json).
It is a snapshot for administrators, not an automatically applied workflow.

The configuration under **Settings → Rules → Rulesets** is:

1. Target: `refs/heads/main`.
2. Enable **Restrict deletions** and **Block force pushes**.
3. Enable **Require a pull request before merging**, with 1 required approval,
   *Dismiss stale approvals*, and *Require conversation resolution*.
4. Enable **Require status checks to pass**, with *Require branches to be up to date*,
   and add: `Quality · lint, format, types, config`, `Summary`, `Package build`,
   `MkDocs strict build`, `Title · issue · version · changelog`,
   `Dependency audit and lockfile integrity`, `CodeQL`.
5. Only **squash merging** is allowed by the PR rule and repository merge settings.
6. Bypass list: **empty**. No user, team, app or administrator currently bypasses these
   rules (`current_user_can_bypass: never`). If emergency bypass is approved later,
   limit it to repository administrators; never grant a general write-role bypass.

All seven names were verified against actual GitHub Actions check runs: six on main
commit `5e4d8a0a49eb6a37b434a1580ab997f41e5a37ee`, and the PR-only governance check on
PR #87 head `f89851bdcb545f1fb44a9880d8fef06203abfa7a`. Each is also declared as a
workflow job name. Required checks are bound to the GitHub Actions app (ID `15368`),
and branches must be up to date. `Summary` covers the test and launcher matrices.

To reproduce, authenticate `gh` as an administrator, inspect existing rulesets to
avoid duplicates, then create with
`gh api --method POST repos/satvikp-coder/PhotoProof/rulesets --input .github/main-ruleset.json`.
For this existing ruleset, use `--method PUT` and append `/26226021` to that endpoint.
Read back both `/rulesets/26226021` and `/rules/branches/main` to verify enforcement.

### Merge settings and private reporting

Verified on **2026-10-30**: `allow_squash_merge=true`, `allow_merge_commit=false`,
`allow_rebase_merge=false`, `squash_merge_commit_title=PR_TITLE`, and
`squash_merge_commit_message=PR_BODY`. The squash title therefore comes from the PR
title, and its body defaults to the PR description.

Private vulnerability reporting was enabled and the API returned `enabled: true`.
The private reporting link in [SECURITY.md](https://github.com/satvikp-coder/PhotoProof/blob/main/SECURITY.md)
is the reporting route. Administrators can reproduce these settings with:

```bash
gh api --method PATCH repos/satvikp-coder/PhotoProof \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY
gh api --method PUT repos/satvikp-coder/PhotoProof/private-vulnerability-reporting
gh api repos/satvikp-coder/PhotoProof/private-vulnerability-reporting
```

### Enforcement verification

On **2026-10-30**, a disposable branch `chore/63-protection-verification` was created
at the main SHA above, with a separate active ruleset copied from the main payload
(only its name and target changed). An attempted fast-forward REST ref update to an
empty child commit was rejected with **HTTP 422**: "Changes must be made through a
pull request" and "7 of 7 required status checks are expected." The authenticated
administrator had no bypass. The temporary ruleset and branch were then deleted.
No update to `main` was attempted. This verifies rejection on the equivalent test
branch, not a literal push rejection on `main`.

On **2026-10-31**, PR #93 had all CI checks successful and GitHub reported
`reviewDecision: REVIEW_REQUIRED` and `mergeStateStatus: BLOCKED`. It remained a
draft, so this is supporting evidence rather than an isolated approval-blocking
test. A second developer is needed to approve a disposable test PR before testing
the failing-check and unresolved-conversation gates independently. Self-approval
cannot establish this behavior. No merge API call was used as a test.

API readback and the isolated rejection test do not verify every acceptance scenario. Do not make a
test push or attempt a merge on `main`. Use a disposable branch and a temporary
ruleset with the same rules targeting only that branch. Confirm a non-bypass user's
direct push is rejected, then open a test PR into it and inspect mergeability with
no approval, a failing required check, and an unresolved review conversation. Do not
merge the test PR. Close it and remove only the temporary ruleset and branch after
recording evidence. A dry-run push alone does not prove server-side enforcement.

Dependabot has no automatic governance exemption: its default titles must still
match Conventional Commits. A maintainer may add `governance:exempt` with an
explanation to skip issue/version/changelog checks; the title check still runs.
This does not bypass approval, conversation resolution or any other required check.
Lockfile regeneration remains tracked separately in #78.

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

Verified on **2026-10-31** using the public RTD API and published sites:

- Project slug: `photoproof`, repository `https://github.com/satvikp-coder/PhotoProof.git`.
- Default branch: `main`; `latest` identifies `main`, is active and built.
- Canonical documentation URL: [photoproof.readthedocs.io](https://photoproof.readthedocs.io/)
  (HTTP 200).
- Successful main build: [35528958](https://app.readthedocs.org/projects/photoproof/builds/35528958/),
  commit `5e4d8a0a49eb6a37b434a1580ab997f41e5a37ee`, finished 2026-10-30.
- Successful PR #93 build: [35530144](https://app.readthedocs.org/projects/photoproof/builds/35530144/),
  commit `962d79938f7d886b015c901e3a03784e876eebe6`, with a successful GitHub status
  `docs/readthedocs.org:photoproof` and [public preview](https://photoproof--93.org.readthedocs.build/)
  (HTTP 200). This demonstrates PR builds are enabled.
- README now links the confirmed documentation URL and project badge (badge endpoint
  also returned HTTP 200). No project URL was inferred from the repository name.

The project was already connected; this work verified it rather than importing a
second project. No authenticated RTD session was needed to verify public build evidence.
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

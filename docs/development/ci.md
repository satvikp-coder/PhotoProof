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

On **2026-10-31**, an actual Git push was also tested against disposable branch
`chore/63-direct-push-test`. Temporary ruleset `26232417` was copied from the **live**
main ruleset, changing only the name and target. Its API reported
`current_user_can_bypass: never` for `satvikp-coder`. The push of commit
`633b3483fd843f00869e79e1f21efc757d3a4466` was rejected with **GH013** and
"Changes must be made through a pull request." The target stayed at main commit
`5e4d8a0a49eb6a37b434a1580ab997f41e5a37ee`. The temporary ruleset and target branch
were deleted. Main's effective rules still return active ruleset `26226021` and
no bypass. This verifies the enforcement mechanism on an equivalent target;
no literal direct push to `main` was attempted.

Non-draft [test PR #97](https://github.com/satvikp-coder/PhotoProof/pull/97) targets
`main` from disposable source branch `chore/63-acceptance-validation`. Its empty
commit `633b3483fd843f00869e79e1f21efc757d3a4466` has exactly the tree of PR #93's
`e57e1901afd556ce8cb932fee969987204424ae5`, but isolates its CI results from #93.
It is a validation fixture and **must never be merged**.

| Scenario | Observed evidence | Independent gate validation |
|---|---|---|
| No approval | Non-draft #97 reports `REVIEW_REQUIRED` and `BLOCKED`, all seven required checks successful, no unresolved threads and no approving review | **PASS**; draft status, failing checks and unresolved threads are excluded as causes |
| Failing required check | Invalid test-PR title caused the real governance [run 41110163904](https://github.com/satvikp-coder/PhotoProof/actions/runs/41110163904) to fail with exit 1; #97 remained `BLOCKED`. Restoring its title produced successful [run 41110285936](https://github.com/satvikp-coder/PhotoProof/actions/runs/41110285936) | **BLOCKED** pending genuine independent approval, to remove the approval confounder |
| Unresolved conversation | [Inline thread](https://github.com/satvikp-coder/PhotoProof/pull/97#discussion_r4427369103), GraphQL ID `PRRT_kwDOVbq_ys6yGla2`, returned `isResolved: false` while #97 was `BLOCKED`; the validation thread was then resolved | **BLOCKED** pending genuine independent approval and otherwise-green checks |

The intentional title failure only affected #97, not #93. No status was fabricated,
no approval was impersonated, no protection was weakened, and no merge endpoint
was invoked. A blocked PR with multiple blockers is not proof of each independent
gate. Leave #93 in draft until the remaining scenarios are demonstrated.

PR #93's CI run [41109493740](https://github.com/satvikp-coder/PhotoProof/actions/runs/41109493740)
completed successfully at head `e57e190`, including `Summary` and all 15 test-matrix
jobs. Its security, docs, governance and RTD checks also succeeded. Subsequent
documentation-only evidence updates must pass their own latest checks before review.

### Independent developer participation (pending)

A developer other than PR author `satvikp-coder`, with permission to submit a
counting review, must participate. Use #97, not #93, for these tests:

1. Confirm #97 is non-draft, has the valid title
   `chore(repo): validate issue 63 protection without merging`, no unresolved
   threads, and **all seven** configured required checks successful (including
   `Summary`). Record its SHA, `REVIEW_REQUIRED` and `BLOCKED`. Inspect with
   `gh pr view 97 --json headRefOid,isDraft,reviewDecision,mergeStateStatus` and
   `gh pr checks 97 --required`; also compare against the seven-check ruleset,
   because a check that has not started may be absent from the command output.
2. Read the actual changes and submit an approval **only if the review warrants
   it**. This must be a genuine independent code review. Do not use another token
   to impersonate a reviewer. Wait for `reviewDecision: APPROVED` and a mergeable
   baseline; do not merge. If the review finds issues, address them normally and
   repeat the baseline after checks and review complete.
3. With that approval and no open threads, set only #97's title to
   `issue 63 deliberate governance validation failure` using `gh pr edit 97 --title`.
   Wait for the new **real** governance check to fail. Record its run URL, head SHA,
   `APPROVED` review decision, other required checks successful and `BLOCKED` merge
   state. Restore the valid title above and wait for the new governance run to pass.
   Do not push a commit, because that correctly dismisses the approval.
4. Once the baseline is otherwise mergeable again, create an inline review
   conversation on #97 describing this conversation-resolution acceptance test.
   Leave it unresolved. Record the thread URL, `isResolved: false`, all required
   checks passing, approval still valid, and `BLOCKED`. Resolve that validation
   thread and confirm the otherwise mergeable state returns. Never click Merge.
5. Add those actual observations and links to #93 and this page. Close #97 without
   merging and delete only `chore/63-acceptance-validation`. Confirm no temporary
   rulesets remain. Wait for #93's latest CI and RTD builds, then mark #93 ready and
   request a genuine independent review of its final diff. Do not merge #93 or
   close issue #63 automatically.

### Windows pytest finding

The local full run produced **233 passed, 1 skipped, 1 failed**. The failure in
`test_safe_join_rejects_symlink_escape` is **WinError 1314** at `Path.symlink_to`,
before `safe_join` executes. A standalone standard-library reproduction using
`tempfile` and `Path.symlink_to`, without importing PhotoProof, produces the same
error. `whoami /priv` does not list `SeCreateSymbolicLinkPrivilege` for the local
process. This is an existing local Windows privilege limitation, not a PR regression.

Both `tests/unit/test_storage.py` and `src/photoproof/core/security/paths.py` have
identical Git blobs on main and this PR. No test was weakened or skipped and no
machine privilege settings were changed. Rely on the actual hosted Windows results:
all Python 3.11–3.15 test jobs passed for PR #93 at `e57e190`. In particular,
[Windows Python 3.12 job 124247920453](https://github.com/satvikp-coder/PhotoProof/actions/runs/41109493740/job/124247920453)
logs show every storage test passed, **170 unit tests passed, 1 macOS-only test
skipped**, and **64 integration/resilience/security/e2e tests passed**. Its workflow
runs the full unit suite, including the symlink-escape test. This provides the
cross-platform evidence without masking the local failure.

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

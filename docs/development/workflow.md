# Contributor work-allocation guide

This page documents how the PhotoProof team picks, claims and parallelizes backlog
issues. It is the short form of the full handbook in
[CONTRIBUTING.md](https://github.com/satvikp-coder/PhotoProof/blob/main/CONTRIBUTING.md).

## Choosing an issue

Start with [`status:ready` issues](https://github.com/satvikp-coder/PhotoProof/issues?q=is%3Aopen+label%3Astatus%3Aready)
that match your area on the [team page](planning/team.md). Prefer issues on the
[critical path](planning/index.md#critical-path) — they unblock other people's work.

Do not start an issue whose label is `status:blocked` — its hard dependencies are not yet
merged. An issue labelled `status:needs-design` cannot be started until the approach is
agreed in the issue comments. `status:ready` means fully specified and unblocked.

## Claiming an issue

1. Comment on the issue saying you will take it.
2. Assign yourself (or ask a maintainer to assign you).
3. Set the label to `status:in-progress`.
4. Work in a branch named `type/issue-description`, for example
   `feat/15-error-level-analysis`.
5. One `status:in-progress` issue per person at a time (at most two). If you go inactive
   for seven days, unassign yourself so someone else can take it.

## Hard versus soft dependencies

Each backlog issue lists two dependency kinds:

| Kind | Meaning | Can you start? |
|---|---|---|
| **Blocked by** | Hard dependency — these issues must be merged first | No |
| **Integrates with** | You can start now against the documented contract; finish the integration later | Yes |

Detectors follow a **two-PR plan**:
1. `algorithm.py` — a pure NumPy function with synthetic tests. This PR uses `Refs`.
2. `processor.py` — the wiring, plus an integration test through the controller. This PR
   uses `Closes`, once #12 exposes images to plugins.

## Handling version and changelog conflicts

- `app.toml` version is the single source of truth. Only the last PR to merge bumps it;
  every other PR rebases and lets the final merger set the version.
- Add `CHANGELOG.md` entries under `## [Unreleased]` in the right section
  (`Added`, `Fixed`, `Changed`, `Security`).
- Rebase before merging to avoid conflicts in `CHANGELOG.md`, `app.toml` and
  `mkdocs.yml` nav.

## When to split an issue

Split an issue when it is `effort:large` ("more than a week") and has separable parts —
for example, an algorithm PR plus a wiring PR, or a CLI command plus a WebKit page. Small
issues (`effort:small`, up to ~2 days) should stay as one PR.

## Weekly sync checklist

- [ ] Review the [backlog page](planning/backlog.md) for newly `status:ready` issues.
- [ ] Rebase any long-lived branches onto `main`.
- [ ] Confirm each person still has at most two `status:in-progress` issues.
- [ ] Check that no `status:blocked` issue can be unblocked by a recently merged PR.

## Worked example: PP-015 (Error Level Analysis)

PP-015 is `feat(plugins): implement Error Level Analysis as a supporting signal`, owned by
Shambhavee, `effort:small`.

1. **Claim:** Shambhavee comments on #15, self-assigns, sets `status:in-progress`.
2. **Branch:** `feat/15-error-level-analysis`.
3. **PR 1 (algorithm):** `src/photoproof/plugins/ela/algorithm.py` — a pure NumPy function
   that takes a decoded image and returns an ELA map. Synthetic tests from the #72
   generators. Title: `feat(plugins): ELA algorithm`. Body: `Refs #15`.
4. **Review and merge:** One approval, CI green, squash-merged into `main`.
5. **PR 2 (processor):** `src/photoproof/plugins/ela/processor.py` — wires the algorithm
   through the controller once #12 exposes decoded images. Title:
   `feat(plugins): ELA processor`. Body: `Closes #15`.
6. **Documentation:** Add the ELA page to `docs/plugins/index.md` nav.
7. **Done:** #15 is closed as satisfied by PR 2.

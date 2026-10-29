# Versioning and releases

## Source of truth

`app.toml` → `[application].version`. `pyproject.toml` declares `dynamic = ["version"]`
and hatchling reads the version from `app.toml` with a regex. Nothing else holds a
version number. CI (`governance.py version --dist dist`) builds the wheel and fails
if its metadata disagrees with `app.toml`, or if `CHANGELOG.md` has no matching or
`Unreleased` section.

## Policy

Semantic Versioning, strictly, including during `0.x`:

- **PATCH:** fixes, compatible security fixes, docs, tests, refactors, CI, logging.
- **MINOR:** new features, algorithms, optional plugin capabilities or formats;
  compatible changes; removing explicitly experimental features. PATCH resets.
- **MAJOR:** breaking a supported contract (public API, plugin API, CLI, config
  format, manifest/artifact format, removing supported functionality). Requires
  maintainer approval and migration notes.

Each merged PR bumps once, using the highest category. The PR-governance workflow
checks that the bump is exactly one valid step and that breaking titles (`!`) are
MAJOR. Plugin versions are independent.

## Release process (maintainers)

1. On `main`, rename `## [Unreleased]` in `CHANGELOG.md` to
   `## [x.y.z] - YYYY-MM-DD` (it must match `app.toml`) and add an empty
   `## [Unreleased]` above it, via a PR.
2. After merge, tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. `release.yml` verifies the tag matches `app.toml`, checks the changelog section,
   refuses duplicate releases, runs the tests, builds the sdist and wheel, and creates
   the GitHub release with the changelog section as notes.

Nothing is published to PyPI. The attached wheel and sdist are build-verification
artifacts: PhotoProof currently runs only from a repository checkout (bootstrap needs
`app.toml`, the configuration directories and the `src/` layout). Running from an
installed wheel is tracked as a backlog issue.

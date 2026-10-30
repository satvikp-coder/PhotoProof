<!--
Title must be a Conventional Commit: type(scope): imperative description
It becomes the squash-merge commit message. See CONTRIBUTING.md.
-->

## Summary

<!-- What does this PR do, in two or three sentences? -->

## Motivation

<!-- Why is the change needed? -->

## Related issue

Closes #

## Scope of changes

- 

## Implementation details

<!-- Design decisions, trade-offs, anything a reviewer needs to know. -->

## Test evidence

<!-- Commands run and their results. New tests that fail without this change. -->

```text
pytest ...
```

## Documentation changes

<!-- List the pages you updated, or say why none needed changing. -->

## Version impact

<!-- Write one: PATCH, MINOR or MAJOR. MAJOR needs maintainer approval and migration notes. -->

`app.toml` version: `x.y.z → x.y.z`

## Changelog entry

<!-- Paste the lines added under ## [Unreleased]. -->

## Compatibility considerations

<!-- Plugin API, CLI, configuration, manifest or artifact format changes? -->

## Security considerations

<!-- Untrusted input, file system, dependencies, privileges, evidence integrity. -->

## Screenshots or execution examples

<!-- Optional. -->

## Checklist

- [ ] Branch follows `type/issue-description`
- [ ] `ruff check .`, `ruff format --check .`, `mypy` and `pytest` pass locally
- [ ] `mkdocs build --strict` passes (if docs changed)
- [ ] No secrets, datasets or user photographs committed

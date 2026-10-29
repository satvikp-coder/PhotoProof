import importlib.util
import sys

from helpers import REPO

_spec = importlib.util.spec_from_file_location("governance", REPO / ".github/scripts/governance.py")
assert _spec and _spec.loader
gov = importlib.util.module_from_spec(_spec)
sys.modules["governance"] = gov
_spec.loader.exec_module(gov)

APP = '[application]\nversion = "{}"\n'
LOG = "# Changelog\n\n## [Unreleased]\n{}\n## [0.1.0] - 2026-10-29\n- init\n"


def _evaluate(**overrides: object) -> tuple[list[str], list[str]]:
    args: dict[str, object] = dict(
        title="feat(pipeline): add dependency-aware scheduling",
        body="Closes #42",
        labels=set(),
        base_app_toml=APP.format("0.1.0"),
        head_app_toml=APP.format("0.2.0"),
        changed={"CHANGELOG.md", "app.toml", "src/x.py", "tests/test_x.py", "docs/x.md"},
        base_changelog=LOG.format(""),
        head_changelog=LOG.format("### Added\n- Scheduler orders plugins by dependency.\n"),
    )
    args.update(overrides)
    return gov.evaluate_pr(**args)  # type: ignore[no-any-return]


def test_valid_pr_passes() -> None:
    assert _evaluate() == ([], [])


def test_title_issue_version_changelog_failures() -> None:
    failures, _ = _evaluate(
        title="Update stuff", body="no ref", head_app_toml=APP.format("0.1.0"), changed={"src/x.py"}
    )
    assert len(failures) == 4


def test_breaking_change_requires_major() -> None:
    failures, _ = _evaluate(title="feat(plugins)!: replace contract")
    assert any("MAJOR" in f for f in failures)
    assert (
        _evaluate(title="feat(plugins)!: replace contract", head_app_toml=APP.format("1.0.0"))[0]
        == []
    )


def test_missing_docs_and_tests_only_warn() -> None:
    failures, warnings = _evaluate(changed={"CHANGELOG.md", "app.toml", "src/x.py"})
    assert failures == [] and len(warnings) == 2


def test_exempt_label() -> None:
    failures, warnings = _evaluate(
        labels={"governance:exempt"}, body="", changed=set(), head_app_toml=APP.format("0.1.0")
    )
    assert failures == [] and warnings


def test_changelog_sections() -> None:
    sections = gov.changelog_sections(LOG.format("- a\n"))
    assert sections["0.1.0"][0] == "2026-10-29" and sections["Unreleased"][1] == "- a"

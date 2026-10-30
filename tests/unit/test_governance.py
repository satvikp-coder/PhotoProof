import importlib.util
import sys
from pathlib import Path

import pytest
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
        head_app_toml=APP.format("0.1.0"),
        changed={"CHANGELOG.md", "src/x.py", "tests/test_x.py", "docs/x.md"},
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
    assert len(failures) == 3  # title, issue reference and changelog; not the version


def test_breaking_change_requires_major() -> None:
    failures, _ = _evaluate(
        title="feat(plugins)!: replace contract", head_app_toml=APP.format("0.2.0")
    )
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


def test_ordinary_pr_needs_no_version_change() -> None:
    assert _evaluate(head_app_toml=APP.format("0.1.0")) == ([], [])


def test_version_change_outside_a_release_only_warns() -> None:
    failures, warnings = _evaluate(head_app_toml=APP.format("0.2.0"))
    assert failures == []
    assert any("Only release PRs" in w for w in warnings)


def test_release_pr_changes_the_version_without_a_warning() -> None:
    assert _evaluate(title="chore(release): 0.2.0", head_app_toml=APP.format("0.2.0")) == ([], [])


def test_version_change_must_still_be_one_valid_step() -> None:
    failures, _ = _evaluate(head_app_toml=APP.format("0.5.0"))
    assert any("one step" in f for f in failures)


def test_breaking_title_without_a_version_change_passes() -> None:
    failures, _ = _evaluate(title="feat(x)!: break it")
    assert failures == []


def test_pull_request_compares_with_where_it_left_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Main has moved to 0.9.0, but this branch forked at 0.1.0 and left the version alone."""
    files = {
        "forkpoint:app.toml": APP.format("0.1.0"),
        "forkpoint:CHANGELOG.md": LOG.format(""),
    }

    def fake_git(*args: str) -> str:
        if args[0] == "merge-base":
            return "forkpoint\n"
        if args[0] == "diff":
            return "CHANGELOG.md src/x.py tests/test_x.py docs/x.md"
        return files[args[1]]  # a read of origin/main:app.toml (0.9.0) would be a KeyError

    monkeypatch.setattr(gov, "_git", fake_git)
    root = tmp_path / "root"
    root.mkdir()
    (root / "app.toml").write_text(APP.format("0.1.0"), encoding="utf-8")
    (root / "CHANGELOG.md").write_text(LOG.format("### Added\n- A real entry.\n"), encoding="utf-8")
    monkeypatch.setattr(gov, "ROOT", root)
    body = tmp_path / "body.md"
    body.write_text("Closes #42", encoding="utf-8")
    assert gov.cmd_pr("origin/main", "feat(x): add it", body, set()) == 0

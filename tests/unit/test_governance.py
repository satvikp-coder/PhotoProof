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


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Closes #42", [42]),
        ("Refs #7 and fixes #8", [7, 8]),
        ("Part of #3. Resolved #4. closes #3", [3, 4]),  # no repeats
        ("mentions #5 without a keyword", []),
        ("", []),
    ],
)
def test_linked_issues(body: str, expected: list[int]) -> None:
    assert gov.linked_issues(body) == expected


@pytest.mark.parametrize(
    ("current", "linked", "expected"),
    [
        (None, [1], 1),
        (None, [None, 2], 2),
        (None, [3, 1, 2], 1),  # the earliest wins when issues disagree
        (None, [None], None),
        (None, [], None),
        (5, [1], None),  # set by hand: never overwritten
    ],
)
def test_choose_milestone(
    current: int | None, linked: list[int | None], expected: int | None
) -> None:
    assert gov.choose_milestone(current=current, linked=linked) == expected


class _FakeGh:
    """Stands in for the gh CLI, recording what would change on GitHub."""

    def __init__(self, milestones: dict[int, int | None], *, write_fails: bool = False) -> None:
        self.milestones = milestones
        self.write_fails = write_fails
        self.writes: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        if "PATCH" in args:
            self.writes.append(args)
            if self.write_fails:
                raise OSError("Resource not accessible by integration")
            return ""
        number = int(args[1].rsplit("/", 1)[1])
        if number not in self.milestones:
            raise OSError("HTTP 404")
        value = self.milestones[number]
        return "" if value is None else str(value)


def _run_milestone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: _FakeGh, body: str, current: str = ""
) -> int:
    monkeypatch.setattr(gov, "_gh", fake)
    path = tmp_path / "body.md"
    path.write_text(body, encoding="utf-8")
    arguments = ["milestone", "--repo", "o/r", "--pull-request", "9", "--current", current]
    return gov.main([*arguments, "--body-file", str(path)])  # type: ignore[no-any-return]


def test_milestone_is_copied_from_the_linked_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGh({4: 1})
    assert _run_milestone(monkeypatch, tmp_path, fake, "Closes #4") == 0
    assert fake.writes == [("api", "-X", "PATCH", "repos/o/r/issues/9", "-F", "milestone=1")]


def test_milestone_set_by_hand_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGh({4: 1})
    assert _run_milestone(monkeypatch, tmp_path, fake, "Closes #4", current="6") == 0
    assert fake.writes == []


@pytest.mark.parametrize("body", ["no reference at all", "Closes #404", "Closes #4"])
def test_nothing_to_copy_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> None:
    fake = _FakeGh({4: None})
    assert _run_milestone(monkeypatch, tmp_path, fake, body) == 0
    assert fake.writes == []


def test_failed_write_fails_the_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeGh({4: 1}, write_fails=True)
    assert _run_milestone(monkeypatch, tmp_path, fake, "Closes #4") == 1


def test_hostile_body_text_only_ever_yields_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGh({})
    body = 'Closes #1; rm -rf / $(whoami) `id` "quoted"\nRefs #../../x'
    assert _run_milestone(monkeypatch, tmp_path, fake, body) == 0
    assert fake.writes == []

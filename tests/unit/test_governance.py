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

    def __init__(
        self, milestones: dict[int, int | None], *, write_fails: bool = False, read_error: str = ""
    ) -> None:
        self.milestones = {9: None, **milestones}  # 9 is the pull request
        self.write_fails = write_fails
        self.read_error = read_error
        self.writes: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        if "PATCH" in args:
            self.writes.append(args)
            if self.write_fails:
                raise OSError("Resource not accessible by integration")
            return ""
        number = int(args[1].rsplit("/", 1)[1])
        if self.read_error and number != 9:
            raise OSError(self.read_error)
        if number not in self.milestones:
            raise OSError("gh: Not Found (HTTP 404)")
        value = self.milestones[number]
        return "" if value is None else str(value)


def _run_milestone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: _FakeGh, body: str
) -> int:
    monkeypatch.setattr(gov, "_gh", fake)
    path = tmp_path / "body.md"
    path.write_text(body, encoding="utf-8")
    arguments = ["milestone", "--repo", "o/r", "--pull-request", "9"]
    return gov.main([*arguments, "--body-file", str(path)])  # type: ignore[no-any-return]


def test_milestone_is_copied_from_the_linked_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGh({4: 1})
    assert _run_milestone(monkeypatch, tmp_path, fake, "Closes #4") == 0
    assert fake.writes == [("api", "-X", "PATCH", "repos/o/r/issues/9", "-F", "milestone=1")]


def test_live_milestone_set_by_a_person_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Set after the event fired: the workflow reads it live, so it still wins."""
    fake = _FakeGh({4: 1, 9: 6})
    assert _run_milestone(monkeypatch, tmp_path, fake, "Closes #4") == 0
    assert fake.writes == []


@pytest.mark.parametrize("body", ["no reference at all", "Closes #404", "Closes #4"])
def test_nothing_to_copy_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> None:
    fake = _FakeGh({4: None})
    assert _run_milestone(monkeypatch, tmp_path, fake, body) == 0
    assert fake.writes == []


@pytest.mark.parametrize(
    "error", ["HTTP 403: API rate limit exceeded", "HTTP 502: Bad Gateway", "connection reset"]
)
def test_lookup_failure_fails_the_job_instead_of_looking_like_no_milestone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: str
) -> None:
    fake = _FakeGh({4: 1}, read_error=error)
    assert _run_milestone(monkeypatch, tmp_path, fake, "Closes #4") == 1
    assert fake.writes == []


def test_partial_lookup_failure_does_not_pick_from_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGh({4: 3})

    def flaky(*args: str) -> str:
        if args[1].endswith("/5"):
            raise OSError("HTTP 502: Bad Gateway")
        return fake(*args)

    monkeypatch.setattr(gov, "_gh", flaky)
    path = tmp_path / "body.md"
    path.write_text("Closes #4 and refs #5", encoding="utf-8")
    assert (
        gov.main(["milestone", "--repo", "o/r", "--pull-request", "9", "--body-file", str(path)])
        == 1
    )
    assert fake.writes == []


def test_deleted_issue_is_treated_as_having_no_milestone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGh({})
    fake.read_error = "gh: Gone (HTTP 410)"
    assert _run_milestone(monkeypatch, tmp_path, fake, "Closes #4") == 0
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

import pytest

from photoproof.versioning.build import build_info, git_commit
from photoproof.versioning.compatibility import config_schema_compatible, plugin_api_compatible
from photoproof.versioning.version import SemVer, bump_kind


def test_parse_and_order() -> None:
    assert SemVer.parse("0.1.0") < SemVer.parse("0.1.1") < SemVer.parse("0.2.0")
    assert str(SemVer.parse("10.20.30")) == "10.20.30"


@pytest.mark.parametrize("text", ["1.0", "01.0.0", "1.0.0-rc1", "v1.0.0", ""])
def test_parse_rejects(text: str) -> None:
    with pytest.raises(ValueError, match=r"MAJOR\.MINOR\.PATCH"):
        SemVer.parse(text)


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        ("0.1.0", "0.1.1", "patch"),
        ("0.1.4", "0.2.0", "minor"),
        ("0.9.2", "1.0.0", "major"),
        ("0.1.0", "0.1.0", None),
        ("0.1.4", "0.2.1", None),  # PATCH must reset
        ("0.1.0", "0.1.2", None),
    ],
)
def test_bump_kind(old: str, new: str, kind: str | None) -> None:
    assert bump_kind(SemVer.parse(old), SemVer.parse(new)) == kind


def test_plugin_api_compatibility() -> None:
    assert plugin_api_compatible("1.0.0", "1.3.0")
    assert not plugin_api_compatible("1.4.0", "1.3.0")
    assert not plugin_api_compatible("2.0.0", "1.0.0")
    assert not plugin_api_compatible("0.1.0", "0.2.0")  # 0.x minor is breaking
    assert config_schema_compatible(1, 1) and not config_schema_compatible(2, 1)


def test_build_info_reads_git_without_subprocess(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text("abc123\n")
    assert git_commit(tmp_path) == "abc123"
    assert build_info(tmp_path)["git_commit"] == "abc123"
    assert git_commit(tmp_path / "missing") is None

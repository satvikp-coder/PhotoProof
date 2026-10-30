"""safe_join on Windows extended-length paths, which resolve can return during a rename."""

from pathlib import PureWindowsPath

from photoproof.core.security.paths import _without_extended_prefix


def test_extended_prefix_is_removed() -> None:
    path = PureWindowsPath("\\\\?\\C:\\runtime\\objects\\ab\\file")
    assert _without_extended_prefix(path) == PureWindowsPath("C:\\runtime\\objects\\ab\\file")  # type: ignore[arg-type]


def test_unc_paths_keep_their_prefix() -> None:
    path = PureWindowsPath("\\\\?\\UNC\\server\\share\\file")
    assert _without_extended_prefix(path) == path  # type: ignore[arg-type]


def test_ordinary_paths_are_unchanged() -> None:
    path = PureWindowsPath("C:\\runtime\\file")
    assert _without_extended_prefix(path) == path  # type: ignore[arg-type]

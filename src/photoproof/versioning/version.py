"""Strict ``MAJOR.MINOR.PATCH`` parsing and comparison (no pre-release/build suffixes yet)."""

import re
from dataclasses import dataclass
from typing import Self

_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass(frozen=True, order=True)
class SemVer:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Self:
        match = _PATTERN.match(text)
        if not match:
            raise ValueError(f"{text!r} is not a MAJOR.MINOR.PATCH version")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def bump_kind(old: SemVer, new: SemVer) -> str | None:
    """Classify ``old -> new`` as major/minor/patch; ``None`` if it is not one valid bump."""
    if new == SemVer(old.major + 1, 0, 0):
        return "major"
    if new == SemVer(old.major, old.minor + 1, 0):
        return "minor"
    if new == SemVer(old.major, old.minor, old.patch + 1):
        return "patch"
    return None

"""Path containment helpers. All runtime paths are built through ``safe_join``."""

from pathlib import Path, PurePath

from photoproof.contracts.exceptions import UnsafePathError


def safe_join(root: Path, *parts: str) -> Path:
    """Join ``parts`` under ``root`` and guarantee the result stays inside it.

    Rejects absolute parts, ``..`` traversal, NUL bytes, empty components, and
    symlinks that resolve outside ``root``.
    """
    for part in parts:
        if not part or "\x00" in part:
            raise UnsafePathError(f"empty or NUL-containing path component: {part!r}")
        pure = PurePath(part)
        # `root` catches Windows root-relative parts like "\\etc" that are not "absolute".
        if pure.is_absolute() or pure.drive or pure.root or ".." in pure.parts:
            raise UnsafePathError(f"path component escapes its root: {part!r}")
    base = root.resolve()
    candidate = base.joinpath(*parts).resolve()
    if candidate != base and base not in candidate.parents:
        raise UnsafePathError(f"{candidate} is outside {base}")
    return candidate

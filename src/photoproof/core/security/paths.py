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
    base = _without_extended_prefix(root.resolve())
    candidate = _without_extended_prefix(base.joinpath(*parts).resolve())
    if candidate != base and base not in candidate.parents:
        raise UnsafePathError(f"{candidate} is outside {base}")
    return candidate


def _without_extended_prefix(path: Path) -> Path:
    """Undo a ``\\\\?\\`` prefix, which marks the same path on Windows.

    ``resolve`` can return it for a file another process is renaming at that moment. Left in
    place, a path inside the root would compare as outside it. UNC paths keep their prefix.
    """
    text = str(path)
    if text.startswith("\\\\?\\") and not text.startswith("\\\\?\\UNC\\"):
        return type(path)(text[4:])
    return path

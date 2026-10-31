"""Conservative local-file inspection for maintenance (never follow redirects)."""

import os
import stat
from pathlib import Path

from photoproof.contracts.exceptions import StorageError
from photoproof.core.security.paths import safe_join


def checked_path(root: Path, relative: str) -> Path:
    """Reject every symlink/reparse component, including redirects within the root."""
    target = safe_join(root, relative)
    lexical = root.absolute().joinpath(relative)
    current = root.absolute()
    for part in ("", *lexical.relative_to(current).parts):
        if part:
            current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise StorageError("maintenance refuses symlinks, junctions and reparse points")
    return target


def identity(path: Path) -> tuple[int, int, int, int]:
    """Identity used to reject changed, linked or non-regular deletion candidates."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StorageError("maintenance requires an ordinary file with exactly one link")
    if getattr(info, "st_file_attributes", 0) & 0x400:
        raise StorageError("maintenance refuses reparse files")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def usage(root: Path) -> int:
    """Logical runtime bytes; deduplicate hard links, fail on unsafe/inaccessible data."""
    if not root.exists():
        return 0
    checked_path(root, ".")
    seen: set[tuple[int, int]] = set()
    total = 0
    for directory, directories, files in os.walk(root, followlinks=False, onerror=_raise):
        directory_before = Path(directory).stat()
        for name in (*directories, *files):
            item = Path(directory) / name
            checked_path(root, item.relative_to(root).as_posix())
            before = item.lstat()
            if stat.S_ISDIR(before.st_mode):
                continue
            if not stat.S_ISREG(before.st_mode):
                raise StorageError("runtime usage contains a non-regular file")
            key = (before.st_dev, before.st_ino)
            if before.st_ino == 0 or key not in seen:
                total += before.st_size
                seen.add(key)
            after = item.lstat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise StorageError("runtime usage changed during inspection; retry when idle")
        directory_after = Path(directory).stat()
        if directory_before.st_mtime_ns != directory_after.st_mtime_ns:
            raise StorageError("runtime directory changed during usage inspection; retry when idle")
    return total


def _raise(error: OSError) -> None:
    raise error

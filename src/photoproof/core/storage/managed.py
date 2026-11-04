"""Conservative local-file inspection for maintenance (never follow redirects)."""

import os
import stat
import sys
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


def require_local_runtime(root: Path) -> None:
    """Reject detectable remote/removable volumes; reliable local locks are required.

    This is not certification of every mount driver. Operators must not place the runtime
    on network storage; macOS ST_LOCAL and Linux mount metadata cover standard mounts.
    """
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415 - Windows-only native API

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        api.GetDriveTypeW.restype = ctypes.c_uint
        if api.GetDriveTypeW(str(root.resolve().anchor)) != 3:  # DRIVE_FIXED
            raise StorageError(
                "deletion requires a fixed local Windows volume; "
                "network/removable storage is unsupported"
            )
    elif sys.platform == "darwin":
        local = getattr(os, "ST_LOCAL", None)
        if local is not None and not os.statvfs(root).f_flag & local:
            raise StorageError("network runtime storage is unsupported for deletion")
    elif sys.platform.startswith("linux"):
        mounts = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        longest, kind = -1, ""
        for line in mounts.splitlines():
            fields = line.split()
            if "-" not in fields or len(fields) < 7:
                continue
            mount = fields[4].replace("\\040", " ").replace("\\134", "\\")
            base = Path(mount)
            target = root.resolve()
            if (base == target or base in target.parents) and len(mount) > longest:
                longest, kind = len(mount), fields[fields.index("-") + 1]
        if (
            longest < 0
            or kind in {"nfs", "nfs4", "cifs", "smb3", "afs", "ceph", "9p"}
            or kind.startswith("fuse.")
        ):
            raise StorageError("network/uncertain runtime filesystem is unsupported for deletion")

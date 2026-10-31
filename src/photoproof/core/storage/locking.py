"""Cooperative local-filesystem locks. Never unlink or steal a lock file.

The OS, not file contents or timestamps, owns the lock and releases it on process death.
Nested calls in one thread may reuse a stronger lock, but may not upgrade a shared one.
All processes must use this protocol; network filesystems are unsupported.
"""

import ctypes
import errno
import math
import os
import stat
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from photoproof.contracts.exceptions import StorageError
from photoproof.core.security.paths import safe_join

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_local = threading.local()
DEFAULT_TIMEOUT = 10.0


def _windows_lock(fd: int, *, exclusive: bool) -> bool:
    if sys.platform == "win32":

        class Overlapped(ctypes.Structure):
            _fields_ = [
                ("internal", ctypes.c_size_t),
                ("internal_high", ctypes.c_size_t),
                ("offset", wintypes.DWORD),
                ("offset_high", wintypes.DWORD),
                ("event", wintypes.HANDLE),
            ]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
        overlapped = Overlapped()
        function = api.LockFileEx
        function.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        ]
        args = (handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlapped))
        function.restype = wintypes.BOOL
        if function(*args):
            return True
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            return False
        raise StorageError(f"Windows runtime lock failed (error {error}); check local permissions")
    raise StorageError("Windows locks requested on another platform")


def _try_lock(fd: int, *, exclusive: bool) -> bool:
    if sys.platform == "win32":
        return _windows_lock(fd, exclusive=exclusive)
    else:
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        return True


@contextmanager
def file_lock(
    root: Path,
    *,
    name: str = ".runtime.lock",
    exclusive: bool,
    timeout: float = DEFAULT_TIMEOUT,
    create: bool = True,
) -> Iterator[None]:
    """Acquire a shared/exclusive OS lock with a bounded wait, or raise StorageError.

    The trusted root may be created for first setup. Existing security descriptors are
    never relaxed. Lock handles are non-inheritable; fork while holding a lock is unsupported.
    """
    if timeout < 0 or not math.isfinite(timeout):
        raise ValueError("lock timeout must be finite and nonnegative")
    try:
        path = _lock_path(root, name, create=create)
    except OSError as error:
        raise StorageError(
            f"{root}: cannot prepare runtime lock: {error}; check local permissions"
        ) from error
    key = (os.getpid(), os.path.normcase(str(path)))
    held: dict[tuple[int, str], bool] = getattr(_local, "held", {})
    if key in held:
        if exclusive and not held[key]:
            raise StorageError(
                f"{path}: cannot upgrade an active shared lock; finish analysis first"
            )
        yield
        return
    descriptor = -1
    try:
        descriptor = os.open(
            path, os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not os.path.samestat(opened, path.stat(follow_symlinks=False))
        ):
            raise StorageError(f"{path}: unsafe or replaced lock file; investigate manually")
        deadline = time.monotonic() + timeout
        while not _try_lock(descriptor, exclusive=exclusive):
            if time.monotonic() >= deadline:
                kind = "exclusive repair" if exclusive else "shared analysis"
                raise StorageError(
                    f"{path}: timed out after {timeout:g}s waiting for {kind} lock; "
                    "finish active work and retry; never remove the lock file"
                )
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise StorageError(
            f"{path}: runtime lock unavailable: {error}; use a local writable volume"
        ) from error
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    held[key] = exclusive
    _local.held = held
    try:
        yield
    finally:
        del held[key]
        os.close(descriptor)  # OS releases the lock, including during exception unwinding


def _lock_path(root: Path, name: str, *, create: bool) -> Path:
    if str(root).startswith("\\\\"):
        raise StorageError(f"{root}: network runtime locking is unsupported; use a local volume")
    if sys.platform == "win32":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        api.GetDriveTypeW.restype = wintypes.UINT
        if api.GetDriveTypeW(root.absolute().anchor) == 4:  # DRIVE_REMOTE
            raise StorageError(f"{root}: mapped network runtime locking is unsupported")
    # Check before resolving: safe_join alone would hide an in-root symlink alias.
    ancestors = (
        (root.absolute(), *root.absolute().parents)
        if sys.platform == "win32"
        else (root.absolute(),)
    )
    for ancestor in ancestors:
        if ancestor.exists():
            info = ancestor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise StorageError(f"{ancestor}: runtime locks refuse reparse/symlink paths")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = root / name
    if raw.is_symlink() or (raw.exists() and getattr(raw.lstat(), "st_file_attributes", 0) & 0x400):
        raise StorageError(f"{raw}: lock file must not be a link")
    path = safe_join(root, name)
    return path

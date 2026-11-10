"""Cross-platform advisory file locking for runtime directories.

Provides shared (read) and exclusive (write) locks on an ``ARE/.lock`` file.
Used by analyses (shared lock) and repairs (exclusive lock) to prevent
concurrent modification of runtime state.

On POSIX, uses fcntl.flock. On Windows, uses a simple polling-based
flag file since fcntl.flock semantics differ.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

_WINDOWS = os.name == "nt"
_LOCK_TIMEOUT = 30.0  # seconds
_POLL_INTERVAL = 0.05  # seconds

if not _WINDOWS:
    import fcntl


class RuntimeLock:
    """Advisory lock on the runtime directory.

    Supports shared (analyses) and exclusive (repairs) locking.
    Multiple shared locks can coexist; an exclusive lock excludes all others.
    """

    def __init__(self, runtime_root: Path, *, exclusive: bool = False) -> None:
        self._lock_file = runtime_root / ".lock"
        self._exclusive = exclusive
        self._fd: int | None = None
        self._acquired = False

    def __enter__(self) -> RuntimeLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    def acquire(self) -> None:
        """Acquire the lock, blocking until available or timeout."""
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + _LOCK_TIMEOUT

        while True:
            try:
                self._fd = os.open(
                    str(self._lock_file),
                    os.O_RDWR | os.O_CREAT,
                    0o600,
                )
            except OSError:
                if time.time() >= deadline:
                    raise
                time.sleep(_POLL_INTERVAL)
                continue

            try:
                if _WINDOWS:
                    # Windows: use a simple flag file approach
                    # Write our PID to indicate we hold the lock
                    os.write(self._fd, f"{os.getpid()}\n".encode())
                    os.fsync(self._fd)
                    self._acquired = True
                else:
                    flags = fcntl.LOCK_EX if self._exclusive else fcntl.LOCK_SH
                    fcntl.flock(self._fd, flags | fcntl.LOCK_NB)
                    self._acquired = True
                return
            except (BlockingIOError, OSError):
                # Lock not available
                with contextlib.suppress(OSError):
                    os.close(self._fd)
                self._fd = None
                if time.time() >= deadline:
                    raise
                time.sleep(_POLL_INTERVAL)

    def release(self) -> None:
        """Release the lock."""
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired


def shared_lock(runtime_root: Path) -> RuntimeLock:
    """Context manager for a shared (read) lock on the runtime directory."""
    return RuntimeLock(runtime_root, exclusive=False)


def exclusive_lock(runtime_root: Path) -> RuntimeLock:
    """Context manager for an exclusive (write) lock on the runtime directory."""
    return RuntimeLock(runtime_root, exclusive=True)

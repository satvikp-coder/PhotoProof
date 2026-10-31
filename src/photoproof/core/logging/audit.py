"""Durable JSONL appends with bounded local OS locking and lossless numbered rotation.

This is not a tamper-evident chain (#81). Network filesystems are unsupported.
"""

import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from photoproof.contracts.exceptions import StorageError
from photoproof.core.storage.managed import checked_path, identity

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    # The lock file is permanent. Removing it would split concurrent lock domains.
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    checked_path(path.parent, path.name)
    descriptor = os.open(path, flags, 0o600)
    try:
        identity(path)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        deadline = time.monotonic() + 5
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if sys.platform == "win32":
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise StorageError(
                        "audit append lock timed out; retry when runtime is idle"
                    ) from error
                time.sleep(0.02)
        try:
            yield
        finally:
            if sys.platform == "win32":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def append_audit(path: Path, record: object, *, max_bytes: int = 5_000_000) -> None:
    """Fsync one complete record; preserve every rotated segment without renumbering.

    Refuse incomplete/invalid existing JSONL rather than appending over crash damage.
    A single oversized record is kept intact in its own segment.
    """
    encoded = (json.dumps(record, default=str, sort_keys=True) + "\n").encode("utf-8")
    with _locked(checked_path(path.parent, ".audit.lock")):
        checked_path(path.parent, path.name)
        if path.exists():
            identity(path)
            # Verify all records in the current segment. Historical segments are immutable
            # to this writer; cryptographic integrity verification belongs to #81.
            raw = path.read_bytes()
            if raw:
                if not raw.endswith(b"\n"):
                    raise StorageError(
                        "audit has an incomplete record; preserve it and investigate"
                    )
                try:
                    for line in raw.splitlines():
                        json.loads(line)
                except (ValueError, UnicodeDecodeError) as error:
                    raise StorageError(
                        "audit has invalid JSONL; preserve it and investigate"
                    ) from error
            if raw and len(raw) + len(encoded) > max_bytes:
                numbers = []
                for item in path.parent.glob(f"{path.name}.*"):
                    suffix = item.name.removeprefix(path.name + ".")
                    if suffix.isdecimal():
                        numbers.append(int(suffix))
                destination = checked_path(
                    path.parent, f"{path.name}.{max(numbers, default=0) + 1}"
                )
                if destination.exists():
                    raise StorageError("audit rotation destination exists; refusing overwrite")
                path.rename(destination)
        flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
        )
        descriptor = os.open(path, flags, 0o600)
        try:
            identity(path)
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written == 0:
                    raise StorageError("audit append made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

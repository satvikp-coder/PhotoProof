"""Runtime storage under ``ARE/``: layout, atomic writes and content-addressed blobs.

Layout (all created with owner-only permissions on POSIX):

    uploads/ quarantine/ objects/ artifacts/ cache/ reports/ manifests/ logs/ database/ tmp/

``objects/`` holds original evidence (see ``evidence.py``) and ``artifacts/`` plugin outputs.
Both are content-addressed (``<kind>/<sha[:2]>/<sha>``) and written read-only.
"""

import errno
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Literal

from photoproof.contracts.exceptions import StorageError
from photoproof.core.security.paths import safe_join
from photoproof.core.storage.locking import file_lock

RUNTIME_DIRS = (
    "uploads",
    "quarantine",
    "objects",
    "artifacts",
    "cache",
    "reports",
    "manifests",
    "logs",
    "database",
    "tmp",
)
BlobKind = Literal["objects", "artifacts"]
_POSIX = os.name == "posix"
_NO_HARD_LINKS = {errno.EPERM, errno.ENOTSUP, errno.ENOSYS, errno.EOPNOTSUPP}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    """Deterministic JSON encoding used for hashing and fingerprints."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Write ``data`` to ``path`` so readers see either the old or the complete new file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if _POSIX:
            tmp.chmod(mode)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Persist a rename or link across power loss. Windows has no directory fsync."""
    if _POSIX:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


class RuntimeStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, *parts: str) -> Path:
        return safe_join(self.root, *parts)

    # ---------------------------------------------------------------- layout

    def missing_dirs(self) -> list[str]:
        return [name for name in RUNTIME_DIRS if not (self.root / name).is_dir()]

    def ensure_layout(self) -> list[str]:
        """Create missing runtime directories; return the ones created."""
        created = self.missing_dirs()
        for name in RUNTIME_DIRS:
            directory = self.root / name
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if _POSIX:
                directory.chmod(0o700)
        return created

    def insecure_dirs(self) -> list[str]:
        """Runtime directories that other users can read or write (POSIX only)."""
        if not _POSIX:
            return []
        return [
            name
            for name in RUNTIME_DIRS
            if (self.root / name).is_dir() and (self.root / name).stat().st_mode & 0o077
        ]

    # ------------------------------------------------------------ JSON files

    def write_json(self, value: object, *parts: str) -> Path:
        with file_lock(self.root, exclusive=False):
            target = self.path(*parts)
            atomic_write(target, json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
        return target

    def read_json(self, *parts: str) -> object:
        target = self.path(*parts)
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise StorageError(f"{target}: not found") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise StorageError(f"{target}: corrupted JSON: {error}") from error

    # ------------------------------------------------- content-addressed blobs

    def blob_path(self, kind: BlobKind, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise StorageError(f"invalid sha256 {sha256!r}")
        return self.path(kind, sha256[:2], sha256)

    def put_blob(self, kind: BlobKind, data: bytes) -> str:
        """Publish immutable content under a shared runtime lock; return its SHA-256."""
        with file_lock(self.root, exclusive=False):
            return self._put_blob(kind, data)

    def _put_blob(self, kind: BlobKind, data: bytes) -> str:
        """Store ``data`` immutably and return its SHA-256. Idempotent."""
        digest = sha256_bytes(data)
        target = self.blob_path(kind, digest)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{digest}.", suffix=".part")
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self.publish_blob(kind, temporary, digest)
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    def publish_blob(self, kind: BlobKind, source: Path, sha256: str) -> bool:
        """Publish a completed staging file without overwrite, excluding concurrent repair."""
        with file_lock(self.root, exclusive=False):
            return self._publish_blob(kind, source, sha256)

    def _publish_blob(self, kind: BlobKind, source: Path, sha256: str) -> bool:
        """Give the fully written ``source`` its content address, read-only.

        Never writes to an existing blob, even in a race with another process. Returns
        True if this call created the blob and False if an intact copy already existed.
        Raises ``StorageError`` if the existing blob fails verification.
        """
        target = self.blob_path(kind, sha256)
        if target.exists() or target.is_symlink():
            return self._existing_blob_is_intact(kind, sha256, target)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _POSIX:
            source.chmod(0o400)
        try:
            if _POSIX:
                os.link(source, target)  # unlike a rename, this fails if the target exists
            else:
                os.rename(source, target)  # on Windows this fails if the target exists
        except FileExistsError:  # another process stored the same content first
            return self._existing_blob_is_intact(kind, sha256, target)
        except OSError as error:
            if error.errno in _NO_HARD_LINKS:
                raise StorageError(
                    f"{target.parent}: this filesystem has no hard links, so blobs cannot be "
                    "stored without risking an overwrite. Move runtime.root to another volume."
                ) from error
            raise
        _fsync_directory(target.parent)
        return True

    def _existing_blob_is_intact(self, kind: BlobKind, sha256: str, target: Path) -> bool:
        if self.verify_blob(kind, sha256):
            return False
        raise StorageError(f"{target}: existing blob fails verification; run --doctor")

    def verify_blob(self, kind: BlobKind, sha256: str) -> bool:
        target = self.blob_path(kind, sha256)
        return target.is_file() and sha256_file(target) == sha256

    def iter_blobs(self, kind: BlobKind) -> Iterator[Path]:
        base = self.root / kind
        if base.is_dir():
            yield from sorted(
                p
                for p in base.glob("??/*")
                if p.is_file() and not (p.name.startswith(".") and p.name.endswith(".part"))
            )

    # ---------------------------------------------------------- temporary files

    @contextmanager
    def temp_file(self, suffix: str = "") -> Iterator[Path]:
        """Hold a shared lock throughout temporary-file use and cleanup."""
        with file_lock(self.root, exclusive=False):
            yield from self._temp_file(suffix)

    def _temp_file(self, suffix: str) -> Iterator[Path]:
        """Owner-only temporary file under ``tmp/``, removed on exit."""
        fd, name = tempfile.mkstemp(dir=self.path("tmp"), suffix=suffix)
        os.close(fd)
        path = Path(name)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    @contextmanager
    def quarantine_file(self) -> Iterator[Path]:
        """Hold a shared lock throughout input staging and cleanup."""
        with file_lock(self.root, exclusive=False):
            yield from self._quarantine_file()

    def _quarantine_file(self) -> Iterator[Path]:
        """Owner-only staging file under ``quarantine/`` with a random name, removed on exit."""
        try:
            fd, name = tempfile.mkstemp(dir=self.path("quarantine"), prefix="staged-")
        except OSError as error:
            raise StorageError(f"cannot create a quarantine file: {error}") from error
        os.close(fd)
        path = Path(name)
        try:
            yield path
        finally:
            with suppress(OSError):  # a leftover is cleaned by runtime.stale_temp
                path.unlink(missing_ok=True)

    def stale_temp_files(self, max_age_hours: int, *, now: float | None = None) -> list[Path]:
        """Old files in ``tmp/`` and ``quarantine/``, plus ``.*.part`` leftovers of interrupted
        atomic writes."""
        cutoff = (time.time() if now is None else now) - max_age_hours * 3600
        candidates: list[Path] = []
        for name in ("tmp", "quarantine"):
            directory = self.root / name
            if directory.is_dir():
                candidates += directory.iterdir()
        candidates += self.root.glob("**/.*.part") if self.root.is_dir() else []
        stale = []
        for path in set(candidates):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    stale.append(path)
            except FileNotFoundError:
                continue  # a concurrent writer completed and removed its staging file
        return sorted(stale)

    def remove_temp_files(self, paths: list[Path]) -> list[Path]:
        """Remove only named staging files under an exclusive runtime lock."""
        with file_lock(self.root, exclusive=True):
            return self._remove_temp_files(paths)

    def _remove_temp_files(self, paths: list[Path]) -> list[Path]:
        """Delete specific temporary files. Refuses anything that is not directly inside
        ``tmp/`` or ``quarantine/``, or an atomic-write leftover (``.*.part``) under the
        runtime root."""
        staging, root = {self.path("tmp"), self.path("quarantine")}, self.root.resolve()
        removed = []
        for path in paths:
            resolved = path.resolve()
            leftover = resolved.name.startswith(".") and resolved.name.endswith(".part")
            if resolved.parent not in staging and not (leftover and root in resolved.parents):
                raise StorageError(f"refusing to delete {resolved}: not a temporary file")
            resolved.unlink(missing_ok=True)
            removed.append(resolved)
        return removed

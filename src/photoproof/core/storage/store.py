"""Runtime storage under ``ARE/``: layout, atomic writes and content-addressed blobs.

Layout (all created with owner-only permissions on POSIX):

    uploads/ quarantine/ objects/ artifacts/ cache/ reports/ manifests/ logs/ database/ tmp/

``objects/`` holds original evidence (see ``evidence.py``) and ``artifacts/`` plugin outputs.
Both are content-addressed (``<kind>/<sha[:2]>/<sha>``) and written read-only.
"""

import errno
import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from photoproof.contracts.exceptions import StorageError
from photoproof.core.security.paths import safe_join
from photoproof.core.storage import windows_acl

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
_WINDOWS = os.name == "nt"
_LOG = logging.getLogger(__name__)


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
        if _WINDOWS and mode == 0o400:
            # Publication occurs within a private parent; seal metadata before returning.
            # A failed seal leaves bytes intact and reports failure, never a successful hash.
            windows_acl.restrict_file(path, readonly=True)
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
        self._publication_lock = threading.RLock()

    def path(self, *parts: str) -> Path:
        return safe_join(self.root, *parts)

    # ---------------------------------------------------------------- layout

    def missing_dirs(self) -> list[str]:
        return [name for name in RUNTIME_DIRS if not (self.root / name).is_dir()]

    def ensure_layout(self) -> list[str]:
        """Create missing runtime directories; return the ones created."""
        created = self.missing_dirs()
        if _WINDOWS:
            try:
                windows_acl.ensure_directory(self.root)
                for name in RUNTIME_DIRS:
                    windows_acl.ensure_directory(self.root / name)
                return created
            except windows_acl.UnsupportedACL as error:
                # Allow diagnosis on unsupported stores, never report ACL protection.
                # put_blob refuses these filesystems before writing evidence bytes.
                _LOG.warning("Runtime ACL protection unavailable: %s", error)
        for name in RUNTIME_DIRS:
            directory = self.root / name
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if _POSIX:
                directory.chmod(0o700)
        return created

    def insecure_dirs(self) -> list[str]:
        """Insecure managed paths (Windows includes files), or POSIX subdirectories.

        Native inspection failures raise StorageError, never an empty healthy list.
        """
        if _WINDOWS:
            if not self.root.exists():
                return []  # absence is covered by runtime.directories
            windows_acl.require_supported_volume(self.root)
            return [
                name
                for name, path, directory, readonly in self._windows_targets()
                if windows_acl.problems(
                    windows_acl.inspect(path),
                    directory=directory,
                    readonly=readonly,
                    protected=name in (".", *RUNTIME_DIRS),
                )
            ]
        if not _POSIX:
            return []
        return [
            name
            for name in RUNTIME_DIRS
            if (self.root / name).is_dir() and (self.root / name).stat().st_mode & 0o077
        ]

    def _windows_targets(self) -> Iterator[tuple[str, Path, bool, bool]]:
        """Only the root and known managed trees; never follow reparse points."""
        if sys.platform != "win32":
            raise windows_acl.UnsupportedACL("native runtime ACL traversal requires Windows")
        yield ".", self.root, True, False
        for name in RUNTIME_DIRS:
            base = self.root / name
            if not base.exists():
                continue
            stack = [base]
            while stack:
                directory = stack.pop()
                # Inspect before enumeration so junctions cannot redirect the traversal.
                windows_acl.inspect(directory)
                yield directory.relative_to(self.root).as_posix(), directory, True, False
                with os.scandir(directory) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        attributes = entry.stat(follow_symlinks=False).st_file_attributes
                        if attributes & windows_acl.REPARSE_POINT:
                            raise StorageError(
                                f"{path}: refusing permission traversal of a reparse point"
                            )
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            relative = path.relative_to(self.root)
                            readonly = (
                                name in {"objects", "artifacts"}
                                and len(relative.parts) == 3
                                and len(path.name) == 64
                                and all(c in "0123456789abcdef" for c in path.name)
                            )
                            yield relative.as_posix(), path, False, readonly

    def restrict_permissions(self, names: list[str]) -> None:
        """Apply the canonical policy only to enumerated managed paths, then verify."""
        if _WINDOWS:
            targets = {
                name: (path, directory, readonly)
                for name, path, directory, readonly in self._windows_targets()
            }
            for name in names:
                if name not in targets:
                    raise StorageError(
                        f"refusing permission repair outside managed runtime: {name}"
                    )
                path, directory, readonly = targets[name]
                if directory:
                    windows_acl.ensure_directory(path)
                else:
                    windows_acl.restrict_file(path, readonly=readonly)
        else:
            for name in names:
                self.path(name).chmod(0o700)

    # ------------------------------------------------------------ JSON files

    def write_json(self, value: object, *parts: str) -> Path:
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
        """Store ``data`` immutably and return its SHA-256. Idempotent."""
        digest = sha256_bytes(data)
        if _WINDOWS and not self.root.exists():
            windows_acl.require_supported_volume(self.root)
            for directory in (self.root, self.root / "tmp", self.root / kind):
                windows_acl.ensure_directory(directory, repair_existing=False)
        self.prepare_blob_write(kind)
        target = self.blob_path(kind, digest)
        if _WINDOWS:
            windows_acl.ensure_directory(target.parent, repair_existing=False, protected=False)
        else:
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

    def prepare_blob_write(self, kind: BlobKind = "objects") -> None:
        """Verify Windows privacy before staging bytes; POSIX publication is unchanged."""
        if _WINDOWS:
            windows_acl.require_supported_volume(self.root)
            for directory in (self.root, self.root / "tmp", self.root / kind):
                if not directory.is_dir():
                    raise StorageError(
                        f"{directory}: cannot create a temporary file; runtime directory missing"
                    )
                windows_acl.ensure_directory(directory, repair_existing=False)

    def publish_blob(self, kind: BlobKind, source: Path, sha256: str) -> bool:
        """Give the fully written ``source`` its content address, read-only.

        Never writes to an existing blob, even in a race with another process. Returns
        True if this call created the blob and False if an intact copy already existed.
        Raises ``StorageError`` if the existing blob fails verification.
        """
        if _WINDOWS:
            with self._publication_lock:
                return self._publish_blob(kind, source, sha256)
        return self._publish_blob(kind, source, sha256)

    def _publish_blob(self, kind: BlobKind, source: Path, sha256: str) -> bool:
        self.prepare_blob_write(kind)
        target = self.blob_path(kind, sha256)
        if target.exists() or target.is_symlink():
            return self._existing_blob_is_intact(kind, sha256, target)
        if _WINDOWS:
            windows_acl.ensure_directory(target.parent, repair_existing=False, protected=False)
            # source is a caller-owned staging file, never the submitted original.
            # A moved file keeps its DACL: privatize staging before publication.
            windows_acl.restrict_file(source, readonly=False)
        else:
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
        if _WINDOWS:
            windows_acl.restrict_file(target, readonly=True)
        return True

    def _existing_blob_is_intact(self, kind: BlobKind, sha256: str, target: Path) -> bool:
        if self.verify_blob(kind, sha256):
            if _WINDOWS:
                issues = windows_acl.problems(
                    windows_acl.inspect(target), directory=False, readonly=True, protected=True
                )
                if issues:
                    raise StorageError(
                        f"{target}: insecure existing blob permissions; run --doctor and repair"
                    )
            return False
        raise StorageError(f"{target}: existing blob fails verification; run --doctor")

    def verify_blob(self, kind: BlobKind, sha256: str) -> bool:
        target = self.blob_path(kind, sha256)
        return target.is_file() and sha256_file(target) == sha256

    def iter_blobs(self, kind: BlobKind) -> Iterator[Path]:
        base = self.root / kind
        if base.is_dir():
            yield from sorted(p for p in base.glob("??/*") if p.is_file())

    # ---------------------------------------------------------- temporary files

    @contextmanager
    def temp_file(self, suffix: str = "") -> Iterator[Path]:
        """Owner-only temporary file under ``tmp/``, removed on exit."""
        fd, name = tempfile.mkstemp(dir=self.path("tmp"), suffix=suffix)
        os.close(fd)
        path = Path(name)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    def stale_temp_files(self, max_age_hours: int, *, now: float | None = None) -> list[Path]:
        """Old files in ``tmp/`` plus ``.*.part`` leftovers of interrupted atomic writes."""
        cutoff = (time.time() if now is None else now) - max_age_hours * 3600
        tmp = self.root / "tmp"
        candidates = list(tmp.iterdir()) if tmp.is_dir() else []
        candidates += self.root.glob("**/.*.part") if self.root.is_dir() else []
        return sorted(p for p in set(candidates) if p.is_file() and p.stat().st_mtime < cutoff)

    def remove_temp_files(self, paths: list[Path]) -> list[Path]:
        """Delete specific temporary files. Refuses anything that is not directly inside
        ``tmp/`` or an atomic-write leftover (``.*.part``) under the runtime root."""
        tmp, root = self.path("tmp"), self.root.resolve()
        removed = []
        for path in paths:
            resolved = path.resolve()
            leftover = resolved.name.startswith(".") and resolved.name.endswith(".part")
            if resolved.parent != tmp and not (leftover and root in resolved.parents):
                raise StorageError(f"refusing to delete {resolved}: not a temporary file")
            resolved.unlink(missing_ok=True)
            removed.append(resolved)
        return removed

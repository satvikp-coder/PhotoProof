"""Original evidence: stored once, byte for byte, and verified on every read.

The storage key is the SHA-256 of the content, so identity and integrity are the same
property. An original is never modified, re-encoded, renamed or deleted here, and its
submitted file name is metadata only. It never contributes to a path.
"""

import hashlib
import io
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from photoproof.contracts.exceptions import InputRejectedError, StorageError
from photoproof.core.database.store import Database
from photoproof.core.security.inputs import open_regular_file
from photoproof.core.storage.store import RuntimeStorage

_CHUNK = 1 << 20
_MAX_NAME_CHARS = 4096
_NOT_A_LINK = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


@dataclass(frozen=True)
class EvidenceRecord:
    sha256: str
    size: int
    first_seen_at: str
    submission_id: int
    is_new: bool  # False when identical bytes were already stored


class EvidenceStore:
    def __init__(
        self, storage: RuntimeStorage, database: Database, *, max_input_bytes: int
    ) -> None:
        self._storage = storage
        self._database = database
        self._max_input_bytes = max_input_bytes

    def ingest(
        self, source: Path | bytes, *, role: str, original_name: str | None = None
    ) -> EvidenceRecord:
        """Store ``source`` immutably and record this submission.

        Identical bytes are stored once, and each ingest adds a submission record. Raises
        ``InputRejectedError`` for unacceptable input and ``StorageError`` if it cannot be
        stored, leaving no partial blob either way.
        """
        if isinstance(source, bytes):
            label = "input"
        else:
            label = str(source)
            original_name = source.name if original_name is None else original_name
        name = _storable_name(original_name)  # before opening, so nothing needs closing
        reader = io.BytesIO(source) if isinstance(source, bytes) else open_regular_file(source)
        with reader:
            sha256, size = self._store(reader, label)
        row = self._database.record_submission(
            sha256=sha256, size=size, role=role, original_name=name
        )
        return EvidenceRecord(
            sha256=sha256,
            size=size,
            first_seen_at=str(row["first_seen_at"]),
            submission_id=int(str(row["submission_id"])),
            is_new=bool(row["is_new"]),
        )

    def open(self, sha256: str) -> BinaryIO:
        """Return the original's bytes, verified. Raises ``StorageError`` on any mismatch.

        The handle that was verified is the handle returned, so replacing the file
        afterwards cannot substitute different bytes.
        """
        target = self._storage.blob_path("objects", sha256)
        try:
            descriptor = os.open(target, os.O_RDONLY | _NOT_A_LINK)
        except OSError as error:
            raise StorageError(f"evidence {sha256} is not readable: {error}") from error
        handle = os.fdopen(descriptor, "rb")
        try:
            digest = hashlib.sha256()
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
            if digest.hexdigest() != sha256:
                raise StorageError(
                    f"evidence {sha256} fails hash verification: the stored original has "
                    "changed. Do not use it; run `python main.py --doctor`."
                )
            handle.seek(0)
        except BaseException:
            handle.close()
            raise
        return handle

    def _store(self, reader: BinaryIO, label: str) -> tuple[str, int]:
        """Copy to ``tmp/`` while hashing, then publish. Returns ``(sha256, size)``."""
        self._storage.prepare_blob_write()  # reject unsupported stores before staging evidence
        try:
            descriptor, name = tempfile.mkstemp(dir=self._storage.path("tmp"), prefix="evidence-")
        except OSError as error:
            raise StorageError(f"cannot create a temporary file: {error}") from error
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                sha256, size = self._copy(reader, output, label)
                output.flush()
                os.fsync(output.fileno())
            self._storage.publish_blob("objects", temporary, sha256)
        except OSError as error:
            raise StorageError(
                f"{label}: could not store the original: {error}. Nothing was stored; "
                "check free disk space and the permissions of the runtime directory."
            ) from error
        finally:  # a hard kill skips this; runtime.stale_temp cleans up after that
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        return sha256, size

    def _copy(self, reader: BinaryIO, output: BinaryIO, label: str) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        while True:
            try:
                chunk = reader.read(_CHUNK)
            except OSError as error:
                raise InputRejectedError(f"{label}: cannot read the input: {error}") from error
            if not chunk:
                break
            size += len(chunk)
            if size > self._max_input_bytes:
                raise InputRejectedError(
                    f"{label}: exceeds resources.max_input_bytes ({self._max_input_bytes} bytes)"
                )
            digest.update(chunk)
            output.write(chunk)
        if size == 0:
            raise InputRejectedError(f"{label}: the file is empty; there is nothing to analyse")
        return digest.hexdigest(), size


def _storable_name(name: str | None) -> str | None:
    """Keep the name as submitted, except what the database cannot hold.

    Non-UTF-8 file names reach Python as lone surrogates, which SQLite cannot store, so
    those bytes become visible escapes.
    """
    if name is None:
        return None
    if len(name) > _MAX_NAME_CHARS:
        raise InputRejectedError(f"file name is longer than {_MAX_NAME_CHARS} characters")
    return name.encode("utf-8", "backslashreplace").decode("utf-8")

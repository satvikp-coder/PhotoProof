"""The one doorway for untrusted files: stage, validate, then promote to evidence.

Bytes are staged in ``quarantine/`` under a random name while the size cap is enforced, so
a claimed length is never trusted. The type is identified from content. Only then are they
promoted to the evidence store. Every rejection is audited, with no file content and no
submitted name, and nothing is left behind.

A slow sender can hold this open indefinitely: a per-request timeout belongs to the caller
that owns the connection (WebKit, #39). The CLI reads local files.
"""

import os
from pathlib import Path
from typing import BinaryIO

from photoproof.contracts.analysis import InputDescriptor
from photoproof.contracts.events import EventType
from photoproof.contracts.exceptions import InputRejectedError, StorageError
from photoproof.core.events.bus import EventBus
from photoproof.core.security.inputs import open_regular_file
from photoproof.core.storage.evidence import EvidenceRecord, EvidenceStore
from photoproof.core.storage.store import RuntimeStorage
from photoproof.imaging.formats import HEADER_BYTES, detect_format

_CHUNK = 1 << 20


class IngestionService:
    def __init__(
        self,
        storage: RuntimeStorage,
        evidence: EvidenceStore,
        bus: EventBus,
        *,
        max_input_bytes: int,
        accepted_formats: tuple[str, ...],
    ) -> None:
        self._storage = storage
        self._evidence = evidence
        self._bus = bus
        self._max_input_bytes = max_input_bytes
        self._accepted_formats = accepted_formats

    def ingest(
        self, stream: BinaryIO, *, declared_name: str | None, role: str
    ) -> tuple[InputDescriptor, EvidenceRecord]:
        """Accept one untrusted stream. Raises ``InputRejectedError`` if it is refused.

        Returns the descriptor for analysis and the stored record, whose submission can be
        linked to a run. ``declared_name`` is metadata only.
        """
        size = 0
        detected: str | None = None
        try:
            with self._storage.quarantine_file() as staged:
                size, detected = self._stage(stream, staged)
                self._validate(size, detected)
                record = self._evidence.ingest(staged, role=role, original_name=declared_name or "")
        except InputRejectedError as error:
            self._audit_rejection(str(error), role=role, size=size, detected_format=detected)
            raise
        descriptor = InputDescriptor(
            role=role, sha256=record.sha256, size=record.size, media_format=record.media_format
        )
        return descriptor, record

    def ingest_path(self, path: Path, *, role: str) -> tuple[InputDescriptor, EvidenceRecord]:
        """Accept one local file through the same checks."""
        try:
            stream = open_regular_file(path)
        except InputRejectedError:
            # Not the error's text: it holds the local path, which the audit trail must not.
            self._audit_rejection(
                "not a readable regular file", role=role, size=0, detected_format=None
            )
            raise
        with stream:
            return self.ingest(stream, declared_name=path.name, role=role)

    def _stage(self, stream: BinaryIO, staged: Path) -> tuple[int, str | None]:
        """Copy until the cap, reading at most one chunk past it. Returns size and format."""
        size = 0
        header = b""
        try:
            output = open(staged, "wb")  # noqa: SIM115 - closed by the with below
        except OSError as error:
            raise StorageError(f"cannot stage the input: {error}") from error
        with output:
            while True:
                try:
                    chunk = stream.read(_CHUNK)
                except OSError as error:
                    raise InputRejectedError(f"cannot read the input: {error}") from error
                if not chunk:
                    break
                size += len(chunk)
                if size > self._max_input_bytes:
                    raise InputRejectedError(
                        f"input exceeds resources.max_input_bytes ({self._max_input_bytes} bytes)"
                    )
                if len(header) < HEADER_BYTES:
                    header += chunk[: HEADER_BYTES - len(header)]
                try:
                    output.write(chunk)
                except OSError as error:
                    raise StorageError(f"cannot stage the input: {error}") from error
            try:
                output.flush()
                os.fsync(output.fileno())
            except OSError as error:
                raise StorageError(f"cannot stage the input: {error}") from error
        return size, detect_format(header)

    def _validate(self, size: int, detected: str | None) -> None:
        if size == 0:
            raise InputRejectedError("the input is empty; there is nothing to analyse")
        if detected is None:
            raise InputRejectedError(
                "the input is not a recognised image format "
                f"(accepted: {', '.join(self._accepted_formats) or 'none'})"
            )
        if detected not in self._accepted_formats:
            raise InputRejectedError(
                f"format {detected} is not accepted by settings.formats.accepted "
                f"({', '.join(self._accepted_formats) or 'none'}); add it there to analyse it"
            )

    def _audit_rejection(
        self, reason: str, *, role: str, size: int, detected_format: str | None
    ) -> None:
        self._bus.emit(
            EventType.INPUT_REJECTED,
            data={
                "reason": reason,
                "role": role,
                "size": size,
                "detected_format": detected_format,
            },
        )

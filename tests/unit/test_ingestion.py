"""The doorway for untrusted files: caps, formats, quarantine, audit entries."""

import errno
import io
import json
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import InputRejectedError, StorageError
from photoproof.core import ingestion as ingestion_module
from photoproof.core.bootstrap import Application
from photoproof.core.database.store import Database
from photoproof.core.events.bus import EventBus
from photoproof.core.health.checks import default_registry
from photoproof.core.health.repair import run_repairs
from photoproof.core.ingestion import IngestionService
from photoproof.core.storage.evidence import EvidenceStore
from photoproof.core.storage.store import RuntimeStorage

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 60
TIFF = b"II*\x00" + b"t" * 60
LIMIT = 4 << 20


class Harness:
    def __init__(self, root: Path) -> None:
        self.storage = RuntimeStorage(root)
        self.storage.ensure_layout()
        self.database = Database.open(root / "database" / "x.db")
        self.bus = EventBus()
        self.events: list[Event] = []
        self.bus.subscribe(self.events.append, EventType.INPUT_REJECTED, name="test")
        self.service = IngestionService(
            self.storage,
            EvidenceStore(self.storage, self.database, max_input_bytes=LIMIT),
            self.bus,
            max_input_bytes=LIMIT,
            accepted_formats=("jpeg", "png"),
        )

    def blobs(self) -> list[Path]:
        return list(self.storage.iter_blobs("objects"))

    def staged(self) -> list[Path]:
        return list((self.storage.root / "quarantine").iterdir())


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    instance = Harness(tmp_path / "ARE")
    yield instance
    instance.database.close()


def ingest(harness: Harness, data: bytes, name: str | None = "photo.jpg") -> object:
    return harness.service.ingest(io.BytesIO(data), declared_name=name, role="target")


class CountingStream:
    """An endless upload, recording how much was actually read from it."""

    def __init__(self, first: bytes = JPEG) -> None:
        self._first = first
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._first or b"z" * size
        self._first = b""
        self.bytes_read += len(chunk)
        return chunk


# ------------------------------------------------------------------------ acceptance


def test_accepted_input_reaches_the_evidence_store_and_leaves_quarantine(
    harness: Harness,
) -> None:
    descriptor, record = harness.service.ingest(
        io.BytesIO(JPEG), declared_name="photo.jpg", role="target"
    )
    assert descriptor.media_format == "jpeg"
    assert descriptor.role == "target"
    assert descriptor.sha256 == record.sha256
    assert descriptor.size == len(JPEG)
    assert [p.name for p in harness.blobs()] == [record.sha256]
    assert harness.staged() == []
    assert harness.events == []


def test_stream_returning_short_reads_is_assembled_correctly(harness: Harness) -> None:
    class ShortReads(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            return super().read(3)

    descriptor, _ = harness.service.ingest(
        cast(BinaryIO, ShortReads(JPEG)), declared_name=None, role="target"
    )
    assert descriptor.media_format == "jpeg"
    assert harness.blobs()[0].read_bytes() == JPEG


def test_exactly_at_the_limit_is_accepted(harness: Harness) -> None:
    data = JPEG + b"x" * (LIMIT - len(JPEG))
    descriptor, _ = harness.service.ingest(io.BytesIO(data), declared_name=None, role="target")
    assert descriptor.size == LIMIT


def test_same_content_twice_is_one_blob(harness: Harness) -> None:
    ingest(harness, JPEG)
    ingest(harness, JPEG)
    assert len(harness.blobs()) == 1


def test_declared_name_is_metadata_and_never_a_path(harness: Harness) -> None:
    _, record = harness.service.ingest(
        io.BytesIO(JPEG), declared_name="../../etc/passwd\n\x1b[31m", role="target"
    )
    rows = harness.database.submissions(record.sha256)
    assert rows[0]["original_name"] == "../../etc/passwd\n\x1b[31m"
    assert harness.staged() == []
    assert [p.name for p in harness.blobs()] == [record.sha256]


def test_concurrent_ingests_of_the_same_content(harness: Harness) -> None:
    errors: list[Exception] = []

    def worker() -> None:
        database = Database.open(harness.storage.root / "database" / "x.db")
        service = IngestionService(
            harness.storage,
            EvidenceStore(harness.storage, database, max_input_bytes=LIMIT),
            harness.bus,
            max_input_bytes=LIMIT,
            accepted_formats=("jpeg",),
        )
        try:
            service.ingest(io.BytesIO(JPEG), declared_name=None, role="target")
        except (InputRejectedError, StorageError, OSError) as error:
            errors.append(error)
        finally:
            database.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(harness.blobs()) == 1
    assert harness.staged() == []


# ------------------------------------------------------------------------- rejection


@pytest.mark.parametrize(
    ("data", "message", "detected"),
    [
        (b"", "empty", None),
        (b"short", "not a recognised image", None),
        (b"just some text, long enough to examine", "not a recognised image", None),
        (b"GIF89a" + b"\x00" * 32, "not a recognised image", None),
        (TIFF, "format tiff is not accepted", "tiff"),
    ],
)
def test_bad_input_is_rejected_audited_and_leaves_nothing(
    harness: Harness, data: bytes, message: str, detected: str | None
) -> None:
    with pytest.raises(InputRejectedError, match=message):
        ingest(harness, data)
    assert harness.blobs() == []
    assert harness.staged() == []
    assert len(harness.events) == 1
    entry = harness.events[0].data
    assert (entry["size"], entry["detected_format"], entry["role"]) == (
        len(data),
        detected,
        "target",
    )


def test_oversized_stream_stops_within_one_chunk_of_the_limit(harness: Harness) -> None:
    stream = CountingStream()
    with pytest.raises(InputRejectedError, match="max_input_bytes"):
        harness.service.ingest(cast(BinaryIO, stream), declared_name=None, role="target")
    assert stream.bytes_read <= LIMIT + ingestion_module._CHUNK
    assert harness.blobs() == []
    assert harness.staged() == []
    assert len(harness.events) == 1
    entry = harness.events[0].data
    assert entry["size"] == stream.bytes_read  # what actually arrived, not zero
    assert entry["detected_format"] == "jpeg"


def test_read_error_mid_stream_is_rejected_and_cleaned_up(harness: Harness) -> None:
    class Failing:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, size: int = -1) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return JPEG
            raise OSError(errno.ECONNRESET, "Connection reset")

    with pytest.raises(InputRejectedError, match="cannot read"):
        harness.service.ingest(cast(BinaryIO, Failing()), declared_name=None, role="target")
    assert harness.staged() == []
    assert len(harness.events) == 1
    entry = harness.events[0].data
    assert (entry["size"], entry["detected_format"]) == (len(JPEG), "jpeg")


def test_interrupt_mid_stream_cleans_up(harness: Harness) -> None:
    class Interrupted:
        def read(self, size: int = -1) -> bytes:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        harness.service.ingest(cast(BinaryIO, Interrupted()), declared_name=None, role="target")
    assert harness.staged() == []


def test_missing_quarantine_directory_is_a_storage_error(harness: Harness) -> None:
    (harness.storage.root / "quarantine").rmdir()
    with pytest.raises(StorageError, match="quarantine"):
        ingest(harness, JPEG)


def test_disk_full_while_staging_is_a_storage_error_and_cleans_up(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_space(_: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ingestion_module.os, "fsync", no_space)
    with pytest.raises(StorageError, match="cannot stage"):
        ingest(harness, JPEG)
    monkeypatch.undo()
    assert harness.staged() == []
    assert harness.blobs() == []


# ----------------------------------------------------------------------- local files


def test_ingest_path_accepts_a_file_and_records_its_name(harness: Harness, tmp_path: Path) -> None:
    path = tmp_path / "local.jpg"
    path.write_bytes(JPEG)
    _, record = harness.service.ingest_path(path, role="reference")
    assert harness.database.submissions(record.sha256)[0]["original_name"] == "local.jpg"
    assert harness.database.submissions(record.sha256)[0]["role"] == "reference"


def test_ingest_path_rejects_and_audits_what_cannot_be_opened(
    harness: Harness, tmp_path: Path
) -> None:
    with pytest.raises(InputRejectedError, match="not a regular file"):
        harness.service.ingest_path(tmp_path / "absent", role="target")
    assert len(harness.events) == 1


def test_rejected_local_path_stays_out_of_the_audit_trail(
    make_app: Callable[..., Application], tmp_path: Path
) -> None:
    app = make_app()
    with pytest.raises(InputRejectedError):
        app.ingestion().ingest_path(tmp_path / "private-name.jpg", role="target")
    audit = (app.storage.root / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert "INPUT_REJECTED" in audit and "private-name" not in audit


def test_stream_without_a_name_stores_none(harness: Harness) -> None:
    _, record = harness.service.ingest(io.BytesIO(JPEG), declared_name=None, role="target")
    assert harness.database.submissions(record.sha256)[0]["original_name"] is None


def test_ingest_path_rejects_symlinks(harness: Harness, tmp_path: Path) -> None:
    source = tmp_path / "real.jpg"
    source.write_bytes(JPEG)
    link = tmp_path / "link.jpg"
    try:
        link.symlink_to(source)
    except OSError as error:
        pytest.skip(f"cannot create symlinks here: {error}")
    with pytest.raises(InputRejectedError, match="not a regular file"):
        harness.service.ingest_path(link, role="target")
    assert harness.blobs() == []
    assert len(harness.events) == 1


def test_ingest_path_closes_the_file_after_a_rejection(harness: Harness, tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes(b"not an image, and long enough to examine")
    with pytest.raises(InputRejectedError):
        harness.service.ingest_path(path, role="target")
    path.unlink()  # fails on Windows if the handle were still open


# ---------------------------------------------------------------- audit and quarantine


def test_rejection_reaches_the_audit_trail_without_content_or_name(
    make_app: Callable[..., Application], tmp_path: Path
) -> None:
    app = make_app()
    declared = "patient-records.txt"
    with pytest.raises(InputRejectedError):
        app.ingestion().ingest(
            io.BytesIO(b"SECRET-CONTENT that is not an image"),
            declared_name=declared,
            role="target",
        )
    audit = (app.storage.root / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(line) for line in audit.splitlines()]
    rejected = [e for e in entries if e.get("type") == "INPUT_REJECTED"]
    assert len(rejected) == 1
    assert "SECRET-CONTENT" not in audit and declared not in audit


def test_quarantine_files_have_random_names_and_owner_only_access(tmp_path: Path) -> None:
    storage = RuntimeStorage(tmp_path / "ARE")
    storage.ensure_layout()
    with storage.quarantine_file() as first, storage.quarantine_file() as second:
        assert first != second
        assert first.parent.name == "quarantine"
        if os.name == "posix":
            assert first.stat().st_mode & 0o777 == 0o600
    assert not first.exists() and not second.exists()


def test_stale_quarantine_file_is_cleaned_by_the_safe_repair(
    make_app: Callable[..., Application],
) -> None:
    app = make_app()
    stale = app.storage.root / "quarantine" / "staged-old"
    stale.write_bytes(b"left over from a crash")
    fresh = app.storage.root / "quarantine" / "staged-new"
    fresh.write_bytes(b"still being uploaded")
    long_ago = 1_000_000_000
    os.utime(stale, (long_ago, long_ago))
    assert default_registry().run(app, "runtime.stale_temp").severity.value == "warning"
    run_repairs(app, default_registry(), allow_controlled=False, dry_run=False)
    assert not stale.exists()
    assert fresh.exists()


def test_remove_temp_files_still_refuses_other_locations(tmp_path: Path) -> None:
    storage = RuntimeStorage(tmp_path / "ARE")
    storage.ensure_layout()
    other = tmp_path / "ARE" / "objects" / "keep"
    other.write_bytes(b"evidence")
    with pytest.raises(StorageError, match="refusing to delete"):
        storage.remove_temp_files([other])
    assert other.exists()

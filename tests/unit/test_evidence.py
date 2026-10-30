"""Original evidence: immutability, deduplication, rejection and failure handling."""

import errno
import hashlib
import io
import os
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from photoproof.contracts.exceptions import (
    DatabaseError,
    InputRejectedError,
    MigrationRequiredError,
    StorageError,
    UnsafePathError,
)
from photoproof.core.database import store as database_store
from photoproof.core.database.store import LATEST_VERSION, Database
from photoproof.core.security.inputs import open_regular_file
from photoproof.core.storage import evidence as evidence_module
from photoproof.core.storage.evidence import EvidenceStore
from photoproof.core.storage.store import RuntimeStorage

CONTENT = b"original evidence bytes"
SHA = hashlib.sha256(CONTENT).hexdigest()
LIMIT = 1024


@pytest.fixture
def storage(tmp_path: Path) -> RuntimeStorage:
    runtime = RuntimeStorage(tmp_path / "ARE")
    runtime.ensure_layout()
    return runtime


@pytest.fixture
def database(storage: RuntimeStorage) -> Iterator[Database]:
    db = Database.open(storage.root / "database" / "x.db")
    yield db
    db.close()


@pytest.fixture
def store(storage: RuntimeStorage, database: Database) -> EvidenceStore:
    return EvidenceStore(storage, database, max_input_bytes=LIMIT)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "photo.jpg"
    path.write_bytes(CONTENT)
    return path


def blobs(storage: RuntimeStorage) -> list[Path]:
    return list(storage.iter_blobs("objects"))


def temporaries(storage: RuntimeStorage) -> list[Path]:
    return list((storage.root / "tmp").iterdir())


def assert_nothing_stored(storage: RuntimeStorage) -> None:
    assert blobs(storage) == []
    assert temporaries(storage) == []


# ------------------------------------------------------------------------ storing


def test_ingest_stores_one_byte_identical_copy_at_its_content_address(
    store: EvidenceStore, storage: RuntimeStorage, source: Path
) -> None:
    record = store.ingest(source, role="target")
    assert (record.sha256, record.size, record.is_new) == (SHA, len(CONTENT), True)
    assert blobs(storage) == [storage.root / "objects" / SHA[:2] / SHA]
    assert blobs(storage)[0].read_bytes() == CONTENT
    assert temporaries(storage) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes; Windows ACLs are tracked in #80")
def test_stored_original_is_read_only_with_one_link(
    store: EvidenceStore, storage: RuntimeStorage, source: Path
) -> None:
    store.ingest(source, role="target")
    status = blobs(storage)[0].stat()
    assert status.st_mode & 0o777 == 0o400
    assert status.st_nlink == 1  # the temporary hard link is gone


def test_ingest_leaves_the_source_untouched(store: EvidenceStore, source: Path) -> None:
    before = source.stat()
    store.ingest(source, role="target")
    assert source.read_bytes() == CONTENT
    assert source.stat().st_mtime_ns == before.st_mtime_ns


def test_ingest_accepts_bytes(store: EvidenceStore, storage: RuntimeStorage) -> None:
    record = store.ingest(CONTENT, role="target", original_name="upload.jpg")
    assert record.sha256 == SHA and len(blobs(storage)) == 1


def test_size_exactly_at_the_limit_is_accepted(store: EvidenceStore) -> None:
    assert store.ingest(b"x" * LIMIT, role="target").size == LIMIT


def test_binary_content_is_preserved_exactly(
    store: EvidenceStore, storage: RuntimeStorage, tmp_path: Path
) -> None:
    content = bytes(range(256)) + b"\r\n\x00\x1a" * 4  # newline and EOF bytes on Windows
    path = tmp_path / "binary"
    path.write_bytes(content)
    record = store.ingest(path, role="target")
    with store.open(record.sha256) as handle:
        assert handle.read() == content


# ------------------------------------------------------------------ deduplication


def test_same_bytes_twice_is_one_blob_and_two_submissions(
    store: EvidenceStore, storage: RuntimeStorage, database: Database, source: Path
) -> None:
    first = store.ingest(source, role="target")
    second = store.ingest(CONTENT, role="reference", original_name="other.jpg")
    assert first.is_new and not second.is_new
    assert first.submission_id != second.submission_id
    assert second.first_seen_at == first.first_seen_at
    assert len(blobs(storage)) == 1
    rows = database.submissions(SHA)
    assert [(r["role"], r["original_name"]) for r in rows] == [
        ("target", "photo.jpg"),
        ("reference", "other.jpg"),
    ]


def test_different_bytes_are_separate_blobs(store: EvidenceStore, storage: RuntimeStorage) -> None:
    store.ingest(b"one", role="target")
    store.ingest(b"two", role="target")
    assert len(blobs(storage)) == 2


def test_concurrent_ingests_of_identical_content_keep_one_intact_blob(
    storage: RuntimeStorage,
) -> None:
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        db = Database.open(storage.root / "database" / "x.db")
        try:
            barrier.wait()
            EvidenceStore(storage, db, max_input_bytes=LIMIT).ingest(CONTENT, role="target")
        except (StorageError, DatabaseError, OSError) as error:
            errors.append(error)
        finally:
            db.close()

    Database.open(storage.root / "database" / "x.db").close()  # create before the race
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert blobs(storage) == [storage.root / "objects" / SHA[:2] / SHA]
    assert storage.verify_blob("objects", SHA)
    assert temporaries(storage) == []
    db = Database.open(storage.root / "database" / "x.db")
    assert len(db.submissions(SHA)) == 8
    db.close()


# ---------------------------------------------------------------------- rejection


def test_oversized_input_is_rejected_and_nothing_is_stored(
    store: EvidenceStore, storage: RuntimeStorage, tmp_path: Path
) -> None:
    path = tmp_path / "big"
    path.write_bytes(b"x" * (LIMIT + 1))
    with pytest.raises(InputRejectedError, match="max_input_bytes"):
        store.ingest(path, role="target")
    assert_nothing_stored(storage)


def test_oversized_input_is_not_read_past_the_limit(
    store: EvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evidence_module, "_CHUNK", 16)
    consumed = io.BytesIO(b"x" * (LIMIT * 10))
    with pytest.raises(InputRejectedError):
        store._copy(consumed, io.BytesIO(), "input")
    assert consumed.tell() <= LIMIT + 16


def test_empty_bytes_are_rejected(store: EvidenceStore, storage: RuntimeStorage) -> None:
    with pytest.raises(InputRejectedError, match="empty"):
        store.ingest(b"", role="target")
    assert_nothing_stored(storage)


def test_empty_file_is_rejected(
    store: EvidenceStore, storage: RuntimeStorage, tmp_path: Path
) -> None:
    path = tmp_path / "empty"
    path.touch()
    with pytest.raises(InputRejectedError, match="empty"):
        store.ingest(path, role="target")
    assert_nothing_stored(storage)


def test_missing_file_is_rejected(store: EvidenceStore, tmp_path: Path) -> None:
    with pytest.raises(InputRejectedError, match="not a regular file"):
        store.ingest(tmp_path / "absent", role="target")


def test_directory_is_rejected(store: EvidenceStore, tmp_path: Path) -> None:
    with pytest.raises(InputRejectedError, match="not a regular file"):
        store.ingest(tmp_path, role="target")


def test_symlink_is_rejected_even_to_a_regular_file(
    store: EvidenceStore, storage: RuntimeStorage, source: Path, tmp_path: Path
) -> None:
    link = tmp_path / "link.jpg"
    try:
        link.symlink_to(source)
    except OSError as error:
        pytest.skip(f"cannot create symlinks here: {error}")
    with pytest.raises(InputRejectedError, match="not a regular file"):
        store.ingest(link, role="target")
    assert_nothing_stored(storage)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_fifo_is_rejected_without_blocking(store: EvidenceStore, tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    outcome: list[BaseException] = []

    def attempt() -> None:
        try:
            open_regular_file(fifo).close()
        except InputRejectedError as error:
            outcome.append(error)

    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "opening a FIFO blocked"
    assert len(outcome) == 1


def test_path_with_a_nul_byte_is_rejected(store: EvidenceStore) -> None:
    with pytest.raises(InputRejectedError):
        store.ingest(Path("bad\x00name"), role="target")


# ------------------------------------------------------------------- original name


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "/abs/path.jpg",
        "..\\..\\windows",
        "con\nsole\x1b[31m",
        "名前.jpg",
        "a\x00b",
    ],
)
def test_name_is_metadata_and_never_builds_a_path(
    store: EvidenceStore, storage: RuntimeStorage, database: Database, name: str
) -> None:
    store.ingest(CONTENT, role="target", original_name=name)
    assert blobs(storage) == [storage.root / "objects" / SHA[:2] / SHA]
    assert database.submissions(SHA)[0]["original_name"] == name


def test_non_utf8_name_is_stored_as_visible_escapes(
    store: EvidenceStore, database: Database
) -> None:
    store.ingest(CONTENT, role="target", original_name="bad\udcffname.jpg")
    assert database.submissions(SHA)[0]["original_name"] == "bad\\udcffname.jpg"


def test_oversized_name_is_rejected(store: EvidenceStore, storage: RuntimeStorage) -> None:
    with pytest.raises(InputRejectedError, match="file name"):
        store.ingest(CONTENT, role="target", original_name="x" * 5000)
    assert_nothing_stored(storage)


def test_name_defaults_to_the_source_file_name(
    store: EvidenceStore, database: Database, source: Path
) -> None:
    store.ingest(source, role="target")
    assert database.submissions(SHA)[0]["original_name"] == "photo.jpg"


def test_bytes_without_a_name_are_allowed(store: EvidenceStore, database: Database) -> None:
    store.ingest(CONTENT, role="target")
    assert database.submissions(SHA)[0]["original_name"] is None


# -------------------------------------------------------------------- verification


def _tamper(path: Path, data: bytes) -> None:
    path.chmod(0o600)
    path.write_bytes(data)
    path.chmod(0o400)


def test_open_returns_verified_bytes(store: EvidenceStore, source: Path) -> None:
    store.ingest(source, role="target")
    with store.open(SHA) as handle:
        assert handle.read() == CONTENT


def test_tampered_original_raises_and_returns_no_bytes(
    store: EvidenceStore, storage: RuntimeStorage, source: Path
) -> None:
    store.ingest(source, role="target")
    _tamper(blobs(storage)[0], b"forged")
    with pytest.raises(StorageError, match="hash verification"):
        store.open(SHA)


def test_truncated_original_raises(
    store: EvidenceStore, storage: RuntimeStorage, source: Path
) -> None:
    store.ingest(source, role="target")
    _tamper(blobs(storage)[0], CONTENT[:-1])
    with pytest.raises(StorageError, match="hash verification"):
        store.open(SHA)


def test_opening_unknown_evidence_raises(store: EvidenceStore) -> None:
    with pytest.raises(StorageError, match="not readable"):
        store.open("0" * 64)


@pytest.mark.parametrize("bad", ["", "short", "../../etc/passwd", "A" * 64, "g" * 64])
def test_opening_a_malformed_hash_raises(store: EvidenceStore, bad: str) -> None:
    with pytest.raises(StorageError, match="invalid sha256"):
        store.open(bad)


def test_opening_a_symlink_in_the_store_raises(
    store: EvidenceStore, storage: RuntimeStorage, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(CONTENT)
    target = storage.root / "objects" / SHA[:2] / SHA
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"cannot create symlinks here: {error}")
    with pytest.raises((StorageError, UnsafePathError)):
        store.open(SHA)


def test_reingesting_over_a_corrupt_blob_refuses_and_changes_nothing(
    store: EvidenceStore, storage: RuntimeStorage, source: Path
) -> None:
    store.ingest(source, role="target")
    blob = blobs(storage)[0]
    _tamper(blob, b"forged")
    with pytest.raises(StorageError, match="fails verification"):
        store.ingest(source, role="target")
    assert blob.read_bytes() == b"forged"  # kept for investigation, never overwritten
    assert temporaries(storage) == []


def test_publish_never_overwrites_a_blob_that_appears_during_the_race(
    storage: RuntimeStorage, tmp_path: Path
) -> None:
    corrupt = storage.root / "objects" / SHA[:2] / SHA
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"forged")
    source = storage.root / "tmp" / "ready"
    source.write_bytes(CONTENT)
    with pytest.raises(StorageError, match="fails verification"):
        storage.publish_blob("objects", source, SHA)
    assert corrupt.read_bytes() == b"forged"


# ------------------------------------------------------------------ failure handling


def test_disk_full_while_syncing_leaves_no_partial_blob(
    store: EvidenceStore, storage: RuntimeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_space(_: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(evidence_module.os, "fsync", no_space)
    with pytest.raises(StorageError, match="could not store the original"):
        store.ingest(CONTENT, role="target")
    monkeypatch.undo()
    assert_nothing_stored(storage)


class _FailingReader(io.BytesIO):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self._error = error
        self._reads = 0

    def read(self, size: int | None = -1) -> bytes:
        self._reads += 1
        if self._reads == 1:
            return b"partial data"
        raise self._error


def test_read_error_mid_copy_rejects_the_input_and_cleans_up(
    store: EvidenceStore, storage: RuntimeStorage
) -> None:
    with pytest.raises(InputRejectedError, match="cannot read"):
        store._store(_FailingReader(OSError(errno.EIO, "I/O error")), "input")
    assert_nothing_stored(storage)


def test_interrupt_mid_copy_cleans_up_and_propagates(
    store: EvidenceStore, storage: RuntimeStorage
) -> None:
    with pytest.raises(KeyboardInterrupt):
        store._store(_FailingReader(KeyboardInterrupt()), "input")
    assert_nothing_stored(storage)


def test_missing_temp_directory_is_a_storage_error(
    store: EvidenceStore, storage: RuntimeStorage
) -> None:
    (storage.root / "tmp").rmdir()
    with pytest.raises(StorageError, match="temporary file"):
        store.ingest(CONTENT, role="target")


def test_database_failure_is_reported_and_retry_succeeds(
    store: EvidenceStore,
    storage: RuntimeStorage,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(**_: object) -> dict[str, object]:
        raise DatabaseError("database is locked")

    with monkeypatch.context() as patch:
        patch.setattr(database, "record_submission", broken)
        with pytest.raises(DatabaseError):
            store.ingest(CONTENT, role="target")
    # The blob is intact, so retrying records the submission without storing a second copy.
    record = store.ingest(CONTENT, role="target")
    assert record.sha256 == SHA and len(blobs(storage)) == 1
    assert len(database.submissions(SHA)) == 1


def test_hard_link_refusal_falls_back_without_losing_the_blob(
    store: EvidenceStore, storage: RuntimeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_links(*_: object, **__: object) -> None:
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(os, "link", no_links)
    store.ingest(CONTENT, role="target")
    assert storage.verify_blob("objects", SHA)
    assert temporaries(storage) == []


def test_other_link_errors_are_storage_errors(
    store: EvidenceStore, storage: RuntimeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("hard links are not used on Windows")

    def read_only_filesystem(*_: object, **__: object) -> None:
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(os, "link", read_only_filesystem)
    with pytest.raises(StorageError, match="could not store"):
        store.ingest(CONTENT, role="target")
    assert_nothing_stored(storage)


# ------------------------------------------------------------------------ database


def test_link_submissions_records_the_run(store: EvidenceStore, database: Database) -> None:
    record = store.ingest(CONTENT, role="target")
    database.create_run("run-1", mode="single", profile="standard", cache_key=None)
    database.link_submissions([record.submission_id], "run-1")
    assert database.submissions(SHA)[0]["run_id"] == "run-1"


def test_link_to_an_unknown_run_fails(store: EvidenceStore, database: Database) -> None:
    record = store.ingest(CONTENT, role="target")
    with pytest.raises(DatabaseError, match="write failed"):
        database.link_submissions([record.submission_id], "no-such-run")


def _version_one_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(database_store.MIGRATIONS[0])
    conn.execute(
        "INSERT INTO runs (run_id, created_at, status, mode, profile) "
        "VALUES ('old', 'then', 'COMPLETED', 'single', 'standard')"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


def test_existing_database_requires_controlled_migration(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    _version_one_database(path)
    with pytest.raises(MigrationRequiredError, match="--repair"):
        Database.open(path)
    assert LATEST_VERSION >= 2


def test_migration_keeps_existing_data_and_backs_up_first(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    _version_one_database(path)
    db = Database.open(path, allow_migration=True)
    assert db.schema_version() == LATEST_VERSION
    assert db.get_run("old") is not None
    assert db.integrity_problems() == []
    assert db.evidence_hashes() == []
    db.close()
    backup = sqlite3.connect(tmp_path / "x.db.v1.bak")
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
    assert backup.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    backup.close()

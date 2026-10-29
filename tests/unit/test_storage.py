import os
import time
from pathlib import Path

import pytest

from photoproof.contracts.exceptions import StorageError, UnsafePathError
from photoproof.core.security.paths import safe_join
from photoproof.core.storage.store import RUNTIME_DIRS, RuntimeStorage, atomic_write, canonical_json


@pytest.fixture
def storage(tmp_path: Path) -> RuntimeStorage:
    store = RuntimeStorage(tmp_path / "ARE")
    store.ensure_layout()
    return store


def test_layout_created_owner_only(tmp_path: Path) -> None:
    store = RuntimeStorage(tmp_path / "ARE")
    assert store.missing_dirs() == list(RUNTIME_DIRS)
    assert store.ensure_layout() == list(RUNTIME_DIRS)
    assert store.missing_dirs() == [] and store.ensure_layout() == []
    if os.name == "posix":
        assert store.insecure_dirs() == []
        (store.root / "logs").chmod(0o777)
        assert store.insecure_dirs() == ["logs"]


def test_blobs_are_content_addressed_and_read_only(storage: RuntimeStorage) -> None:
    sha = storage.put_blob("artifacts", b"hello")
    assert storage.put_blob("artifacts", b"hello") == sha  # idempotent
    path = storage.blob_path("artifacts", sha)
    assert path.read_bytes() == b"hello" and storage.verify_blob("artifacts", sha)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o400


def test_corrupt_blob_never_overwritten(storage: RuntimeStorage) -> None:
    sha = storage.put_blob("objects", b"evidence")
    path = storage.blob_path("objects", sha)
    path.chmod(0o600)
    path.write_bytes(b"tampered")
    assert not storage.verify_blob("objects", sha)
    with pytest.raises(StorageError, match="fails verification"):
        storage.put_blob("objects", b"evidence")
    assert path.read_bytes() == b"tampered"


def test_invalid_blob_hash(storage: RuntimeStorage) -> None:
    with pytest.raises(StorageError):
        storage.blob_path("objects", "../../etc/passwd")


def test_atomic_write_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.json"
    atomic_write(target, b"old")

    def broken_fsync(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        atomic_write(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_json_roundtrip_and_corruption(storage: RuntimeStorage) -> None:
    storage.write_json({"b": 1, "a": 2}, "reports", "x.json")
    assert storage.read_json("reports", "x.json") == {"a": 2, "b": 1}
    (storage.root / "reports" / "x.json").write_text("{not json")
    with pytest.raises(StorageError, match="corrupted"):
        storage.read_json("reports", "x.json")


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"a": 1, "b": [1, 2]}) == canonical_json({"b": [1, 2], "a": 1})


def test_temp_files(storage: RuntimeStorage) -> None:
    with storage.temp_file(".x") as path:
        assert path.parent == storage.path("tmp") and path.exists()
    assert not path.exists()
    old = storage.path("tmp") / "old.part"
    old.write_text("x")
    past = time.time() - 48 * 3600
    os.utime(old, (past, past))
    (storage.path("tmp") / "new.part").write_text("x")
    stale = storage.stale_temp_files(24)
    assert [p.name for p in stale] == ["old.part"]
    storage.remove_temp_files(stale)
    assert not old.exists()


def test_remove_temp_files_refuses_outside_tmp(storage: RuntimeStorage) -> None:
    victim = storage.root / "objects" / "keep"
    victim.write_text("evidence")
    with pytest.raises(StorageError, match="refusing"):
        storage.remove_temp_files([victim])
    assert victim.exists()


@pytest.mark.parametrize("part", ["../x", "/etc/passwd", "a/../../b", "", "a\x00b"])
def test_safe_join_rejects(tmp_path: Path, part: str) -> None:
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, part)


def test_safe_join_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(UnsafePathError):
        safe_join(root, "link", "x")
    assert safe_join(root, "a", "b") == (root / "a" / "b").resolve()

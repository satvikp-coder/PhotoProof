"""Windows may report access denied when a read-only blob wins a rename race."""

import errno
import hashlib

import pytest

from photoproof.contracts.exceptions import StorageError
from photoproof.core.storage import store as storage_module
from photoproof.core.storage.store import RuntimeStorage

CONTENT = b"deterministic duplicate evidence bytes"
SHA = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def storage(app_root):
    runtime = RuntimeStorage(app_root / "ARE")
    runtime.ensure_layout()
    return runtime


def _windows_race(storage, monkeypatch, *, winner):
    monkeypatch.setattr(storage_module, "_POSIX", False)
    monkeypatch.setattr(storage_module, "_WINDOWS", True)
    cleared = []
    monkeypatch.setattr(storage_module, "make_file_read_only", lambda path: None)
    monkeypatch.setattr(storage_module, "clear_file_read_only", cleared.append)
    source = storage.path("tmp", "staged-evidence")
    source.write_bytes(CONTENT)
    target = storage.blob_path("objects", SHA)

    def denied_after_competitor_publishes(src, dst):
        assert src == source and dst == target
        if winner is not None:
            dst.write_bytes(winner)
        raise PermissionError(errno.EACCES, "Access is denied", str(dst))

    monkeypatch.setattr(storage_module.os, "rename", denied_after_competitor_publishes)
    return source, target, cleared


def test_readonly_winner_is_deduplicated_only_after_hash_verification(storage, monkeypatch):
    source, target, cleared = _windows_race(storage, monkeypatch, winner=CONTENT)
    assert storage.publish_blob("objects", source, SHA) is False
    assert target.read_bytes() == CONTENT and source.read_bytes() == CONTENT
    assert cleared == [str(source)]  # Existing evidence permissions/bytes are untouched.


def test_permission_collision_with_different_bytes_remains_an_error(storage, monkeypatch):
    source, target, _ = _windows_race(storage, monkeypatch, winner=b"unexpected bytes")
    with pytest.raises(StorageError, match="fails verification"):
        storage.publish_blob("objects", source, SHA)
    assert target.read_bytes() == b"unexpected bytes" and source.read_bytes() == CONTENT


def test_permission_denial_without_a_winner_is_not_swallowed(storage, monkeypatch):
    source, target, cleared = _windows_race(storage, monkeypatch, winner=None)
    with pytest.raises(PermissionError):
        storage.publish_blob("objects", source, SHA)
    assert not target.exists() and source.read_bytes() == CONTENT and not cleared

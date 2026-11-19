"""Windows may report access denied when a read-only blob wins a rename race."""

import errno
import hashlib
import os

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


@pytest.mark.parametrize("transient_denials", [1, 3])
def test_windows_winner_is_verified_after_transient_read_denial(
    storage, monkeypatch, transient_denials
):
    target = storage.blob_path("objects", SHA)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(CONTENT)
    source = storage.path("tmp", "second-upload")
    source.write_bytes(CONTENT)
    original_hash = storage_module.sha256_file
    calls = []

    def transient_hash(path):
        calls.append(path)
        if len(calls) <= transient_denials:
            raise PermissionError(errno.EACCES, "transient sharing denial", str(path))
        return original_hash(path)

    monkeypatch.setattr(storage_module, "_WINDOWS", True)
    monkeypatch.setattr(storage_module, "sha256_file", transient_hash)
    monkeypatch.setattr(storage_module.time, "sleep", lambda seconds: None)
    assert storage.publish_blob("objects", source, SHA) is False
    assert len(calls) > transient_denials
    assert target.read_bytes() == CONTENT and source.read_bytes() == CONTENT


@pytest.mark.parametrize("windows", [True, False])
def test_persistent_verification_denial_is_bounded_and_preserves_both_files(
    storage, monkeypatch, windows
):
    target = storage.blob_path("objects", SHA)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(CONTENT)
    source = storage.path("tmp", "denied-upload")
    source.write_bytes(CONTENT)
    calls = []

    def denied_hash(path):
        calls.append(path)
        assert len(calls) <= 10, "verification retry must be bounded"
        raise PermissionError(errno.EACCES, "persistent read denial", str(path))

    monkeypatch.setattr(storage_module, "_WINDOWS", windows)
    monkeypatch.setattr(storage_module, "sha256_file", denied_hash)
    monkeypatch.setattr(storage_module.time, "sleep", lambda seconds: None)
    with pytest.raises(PermissionError, match="persistent"):
        storage.publish_blob("objects", source, SHA)
    if not windows:
        assert len(calls) == 1
    assert target.read_bytes() == CONTENT and source.read_bytes() == CONTENT


def test_corrupt_winner_does_not_retry_or_repair_after_sharing_denial(storage, monkeypatch):
    target = storage.blob_path("objects", SHA)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"corrupt winner")
    source = storage.path("tmp", "unpublished-upload")
    source.write_bytes(CONTENT)
    original_hash = storage_module.sha256_file
    calls = []

    def transient_hash(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError(errno.EACCES, "sharing denial", str(path))
        assert len(calls) == 2, "hash mismatch must not be retried"
        return original_hash(path)

    monkeypatch.setattr(storage_module, "_WINDOWS", True)
    monkeypatch.setattr(storage_module, "sha256_file", transient_hash)
    monkeypatch.setattr(storage_module.time, "sleep", lambda seconds: None)
    with pytest.raises(StorageError, match="fails verification"):
        storage.publish_blob("objects", source, SHA)
    assert len(calls) == 2
    assert target.read_bytes() == b"corrupt winner" and source.read_bytes() == CONTENT


@pytest.mark.skipif(os.name != "nt", reason="actual Windows file-sharing semantics")
def test_actual_windows_sharing_denial_is_reverified_after_handle_release(storage, monkeypatch):
    import ctypes
    from ctypes import wintypes

    target = storage.blob_path("objects", SHA)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(CONTENT)
    source = storage.path("tmp", "sharing-upload")
    source.write_bytes(CONTENT)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    # A real OS handle denies concurrent reads until it closes. No hash/error mock.
    handle = kernel32.CreateFileW(str(target), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    closed = False

    def release_sharing_handle(seconds):
        nonlocal closed
        assert not closed
        assert kernel32.CloseHandle(handle)
        closed = True

    monkeypatch.setattr(storage_module.time, "sleep", release_sharing_handle)
    try:
        assert storage.publish_blob("objects", source, SHA) is False
    finally:
        if not closed:
            kernel32.CloseHandle(handle)
    assert closed and target.read_bytes() == CONTENT and source.read_bytes() == CONTENT

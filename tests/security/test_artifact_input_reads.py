"""Artifact capabilities never authorize arbitrary hashes or unverified byte buffers."""

import pytest

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import PolicyError, StorageError
from photoproof.core.pipeline import artifact_inputs
from photoproof.core.pipeline.artifact_inputs import make_artifact_reader
from photoproof.core.storage.store import RuntimeStorage


def _reader(tmp_path, *, limit=64, staged=True, producer="producer.array"):
    storage = RuntimeStorage(tmp_path / "runtime")
    storage.ensure_layout()
    data = b"verified numerical artifact bytes"
    digest = storage.put_blob("artifacts", data)
    ref = ArtifactRef(
        name="array.npy",
        sha256=digest,
        size=len(data),
        media_type="application/x-npy",
        producer=producer,
    )
    inputs = {"producer.array": {"artifact": ref.model_dump()}}
    reader = make_artifact_reader(
        inputs=inputs,
        producers={"producer.array": "producer.array"},
        staged={producer: [ref]} if staged else {},
        storage=storage,
        max_bytes=limit,
    )
    return reader, storage.blob_path("artifacts", digest), data, inputs


def test_read_verifies_actual_bytes_and_returns_immutable_value(tmp_path):
    read, path, data, _ = _reader(tmp_path)
    assert read("producer.array", 64) == data
    assert path.read_bytes() == data


@pytest.mark.parametrize("staged,producer", [(False, "producer.array"), (True, "foreign.producer")])
def test_foreign_or_unwritten_reference_is_not_authorized(tmp_path, staged, producer):
    read, _, _, _ = _reader(tmp_path, staged=staged, producer=producer)
    with pytest.raises(PolicyError, match="not an authorized"):
        read("producer.array", 64)


def test_undeclared_names_and_later_context_mutations_cannot_grant_access(tmp_path):
    read, _, data, inputs = _reader(tmp_path)
    inputs["private.other"] = inputs["producer.array"]
    with pytest.raises(PolicyError, match="not an authorized"):
        read("private.other", 64)
    assert read("producer.array", 64) == data


def test_declared_size_is_checked_before_open(tmp_path, monkeypatch):
    read, _, _, _ = _reader(tmp_path)
    calls = []
    monkeypatch.setattr(artifact_inputs, "open_regular_file", calls.append)
    with pytest.raises(PolicyError, match="read ceiling"):
        read("producer.array", 1)
    assert not calls


def test_host_ceiling_cannot_be_raised_by_the_plugin(tmp_path):
    read, _, _, _ = _reader(tmp_path, limit=16)
    with pytest.raises(PolicyError, match="16-byte read ceiling"):
        read("producer.array", 1 << 30)


def test_truncated_artifact_is_preserved(tmp_path):
    read, path, data, _ = _reader(tmp_path)
    path.chmod(0o600)
    path.write_bytes(data[:3])
    with pytest.raises(StorageError, match="changed size"):
        read("producer.array", 64)
    assert path.read_bytes() == data[:3]


def test_symbolic_link_is_rejected(tmp_path):
    read, path, data, _ = _reader(tmp_path)
    target = tmp_path / "separate.npy"
    target.write_bytes(data)
    path.chmod(0o600)
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError as error:
        pytest.skip(f"cannot create a symbolic link on this host: {error}")
    with pytest.raises(StorageError):
        read("producer.array", 64)
    assert target.read_bytes() == data and path.is_symlink()


def test_same_size_tampering_is_preserved_and_rejected(tmp_path):
    read, path, data, _ = _reader(tmp_path)
    path.chmod(0o600)
    altered = b"x" * len(data)
    path.write_bytes(altered)
    with pytest.raises(StorageError, match="hash verification"):
        read("producer.array", 64)
    assert path.read_bytes() == altered


def test_missing_artifact_is_not_recreated(tmp_path):
    read, path, _, _ = _reader(tmp_path)
    path.chmod(0o600)
    path.unlink()
    with pytest.raises(StorageError, match="cannot be read"):
        read("producer.array", 64)
    assert not path.exists()


def test_growth_after_fstat_is_bounded_while_reading(tmp_path, monkeypatch):
    read, path, data, _ = _reader(tmp_path, limit=64)
    path.chmod(0o600)
    original_open = artifact_inputs.open_regular_file

    class GrowingFile:
        def __init__(self):
            self.handle = original_open(path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def read(self, size):
            with path.open("ab") as writer:
                writer.write(b"x" * 100)
            return self.handle.read(size)

    monkeypatch.setattr(artifact_inputs, "open_regular_file", lambda _: GrowingFile())
    with pytest.raises(StorageError, match="grew beyond"):
        read("producer.array", 64)
    assert path.stat().st_size > len(data)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_plugin_limits_are_rejected(tmp_path, limit):
    read, _, _, _ = _reader(tmp_path)
    with pytest.raises(PolicyError, match="positive integer"):
        read("producer.array", limit)


@pytest.mark.parametrize("selector", ["map", [-1], (-1,), (True,), ("map",) * 9])
def test_invalid_selectors_are_rejected_before_read(tmp_path, selector):
    read, _, _, _ = _reader(tmp_path)
    with pytest.raises(PolicyError, match="selector"):
        read("producer.array", 64, selector)

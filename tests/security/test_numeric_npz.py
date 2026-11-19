"""Numeric archive reads are exact-schema, bounded and pickle-free."""

import io
import warnings
import zipfile

import numpy as np
import pytest

from photoproof.contracts.arrays import decode_npy, decode_npz_numeric
from photoproof.contracts.exceptions import InputRejectedError


@pytest.mark.parametrize("compressed", [False, True])
def test_numeric_archive_round_trip_including_empty_features(compressed):
    buffer = io.BytesIO()
    arrays = {
        "keypoints": np.zeros((0, 5), np.float32),
        "descriptors": np.zeros((0, 128), np.float32),
    }
    (np.savez_compressed if compressed else np.savez)(buffer, **arrays)
    actual = decode_npz_numeric(buffer.getvalue(), names=frozenset(arrays))
    for name, expected in arrays.items():
        np.testing.assert_array_equal(actual[name], expected)
        assert not actual[name].flags.writeable


def test_empty_feature_opt_in_does_not_make_empty_score_maps_valid():
    buffer = io.BytesIO()
    np.save(buffer, np.zeros((0, 128), np.float32), allow_pickle=False)
    with pytest.raises(InputRejectedError, match="shape"):
        decode_npy(buffer.getvalue())
    assert decode_npy(buffer.getvalue(), allow_empty=True).shape == (0, 128)


@pytest.mark.parametrize(
    "names", [["../values.npy"], ["values.npy", "extra.npy"], ["values.npy", "values.npy"]]
)
def test_unexpected_or_duplicate_archive_names_fail(names):
    payload = io.BytesIO()
    np.save(payload, np.ones(1), allow_pickle=False)
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in names:
                archive.writestr(name, payload.getvalue())
    with pytest.raises(InputRejectedError, match="schema"):
        decode_npz_numeric(buffer.getvalue(), names=frozenset({"values"}))


def test_expanded_size_rejected_before_reading_members(monkeypatch):
    buffer = io.BytesIO()
    np.savez_compressed(buffer, values=np.zeros(10000, np.float32))
    assert len(buffer.getvalue()) < 1024

    def forbidden(*args, **kwargs):
        pytest.fail("oversized members must not be opened or decompressed")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    with pytest.raises(InputRejectedError, match="expanded size"):
        decode_npz_numeric(buffer.getvalue(), names=frozenset({"values"}), max_bytes=1024)


def test_object_archive_is_rejected_without_pickle_loading():
    buffer = io.BytesIO()
    np.savez(buffer, values=np.array([{"not": "numeric"}], dtype=object))
    with pytest.raises(InputRejectedError, match="pickle"):
        decode_npz_numeric(buffer.getvalue(), names=frozenset({"values"}))


def test_corrupt_member_crc_fails_closed():
    buffer = io.BytesIO()
    np.savez(buffer, values=np.ones(8, dtype=np.float32))
    data = bytearray(buffer.getvalue())
    start = data.index(b"\x93NUMPY")
    data[start + 10] ^= 1
    with pytest.raises(InputRejectedError, match="invalid numeric NPZ"):
        decode_npz_numeric(bytes(data), names=frozenset({"values"}))

"""Array headers cannot request unchecked allocations or object deserialization."""

import io

import numpy as np
import pytest

from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.exceptions import InputRejectedError


def _npy(array):
    stream = io.BytesIO()
    np.save(stream, array)
    return stream.getvalue()


@pytest.mark.parametrize("fortran", [False, True])
@pytest.mark.parametrize("dtype", ["bool", "uint8", ">i4", "<f8"])
def test_numerical_roundtrip_is_read_only(fortran, dtype):
    original = np.asarray([[0, 1, 1], [1, 0, 1]], dtype=dtype, order="F" if fortran else "C")
    result = decode_npy(_npy(original), expected_ndim=2)
    np.testing.assert_array_equal(result, original)
    assert result.dtype == original.dtype
    with pytest.raises(ValueError):
        result[0, 0] = 1


@pytest.mark.parametrize(
    "array",
    [np.array([object()], dtype=object), np.zeros(2, dtype=[("x", "f4")]), np.zeros(2, "c8")],
)
def test_nonplain_dtypes_are_rejected(array):
    with pytest.raises(InputRejectedError, match="dtype"):
        decode_npy(_npy(array))


def test_shape_bomb_is_rejected_before_any_array_construction(monkeypatch):
    stream = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        stream, {"descr": "<f8", "fortran_order": False, "shape": (10**12, 10**12)}
    )

    def forbidden(*args, **kwargs):
        pytest.fail("unchecked shape reached array construction")

    monkeypatch.setattr(np, "ndarray", forbidden)
    with pytest.raises(InputRejectedError, match="allocation ceiling"):
        decode_npy(stream.getvalue())


@pytest.mark.parametrize("change", [lambda b: b[:-1], lambda b: b + b"trailing"])
def test_exact_payload_length_is_required(change):
    with pytest.raises(InputRejectedError, match="payload size"):
        decode_npy(change(_npy(np.ones((2, 3), dtype=np.float32))))


@pytest.mark.parametrize(
    "data", [b"", b"not NPY", b"\x93NUMPY\x03\x00", b"\x93NUMPY\x01\x00\xff\xff"]
)
def test_invalid_or_unsupported_headers_are_rejected(data):
    with pytest.raises(InputRejectedError, match="header"):
        decode_npy(data)


def test_dimensions_and_ceilings_are_enforced():
    data = _npy(np.ones((2, 3), dtype=np.float32))
    with pytest.raises(InputRejectedError, match="dimensions"):
        decode_npy(data, expected_ndim=1)
    with pytest.raises(InputRejectedError, match="byte ceiling"):
        decode_npy(data, max_bytes=len(data) - 1)
    with pytest.raises(InputRejectedError, match="allocation ceiling"):
        decode_npy(data, max_elements=5)

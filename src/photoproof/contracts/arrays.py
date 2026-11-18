"""Bounded parsing of numerical NPY artifacts, without pickle or filesystem access."""

import io
import math

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError


def decode_npy(
    data: bytes,
    *,
    max_bytes: int = 64 << 20,
    max_elements: int = 16_777_216,
    expected_ndim: int | None = None,
) -> np.ndarray:
    """Validate the complete header and payload before exposing a read-only array.

    Only plain bool/integer/float arrays in NPY versions 1 and 2 are supported.
    The returned view shares immutable input bytes; parsing never allocates an
    array using an unchecked shape. Callers must separately obtain verified bytes
    through their declared artifact capability.
    """
    for name, limit in (("max_bytes", max_bytes), ("max_elements", max_elements)):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise InputRejectedError(f"NPY {name} must be a positive integer")
    if expected_ndim is not None and (
        isinstance(expected_ndim, bool)
        or not isinstance(expected_ndim, int)
        or not 1 <= expected_ndim <= 8
    ):
        raise InputRejectedError("NPY expected_ndim must be an integer from 1 to 8")
    if not isinstance(data, bytes) or len(data) > max_bytes:
        raise InputRejectedError("NPY input must be immutable bytes within the byte ceiling")
    stream = io.BytesIO(data)
    try:
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(
                stream, max_header_size=10_000
            )
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(
                stream, max_header_size=10_000
            )
        else:
            raise ValueError("unsupported NPY version")
    except (ValueError, TypeError, EOFError, OverflowError) as error:
        raise InputRejectedError(
            "invalid or oversized NPY header; inspect producer output"
        ) from error
    if (
        not 1 <= len(shape) <= 8
        or any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in shape)
        or (expected_ndim is not None and len(shape) != expected_ndim)
    ):
        raise InputRejectedError("NPY shape is empty, invalid or has unexpected dimensions")
    if (
        dtype.hasobject
        or dtype.fields is not None
        or dtype.subdtype is not None
        or dtype.kind not in "biuf"
        or dtype.itemsize not in (1, 2, 4, 8)
    ):
        raise InputRejectedError(
            "NPY dtype must be plain bool, integer or float; pickle is forbidden"
        )
    elements = math.prod(shape)
    size = elements * dtype.itemsize
    if elements > max_elements or size > max_bytes:
        raise InputRejectedError("NPY shape exceeds the numerical allocation ceiling")
    offset = stream.tell()
    if len(data) - offset != size:
        raise InputRejectedError(
            "NPY payload size disagrees with its header; preserve the artifact"
        )
    result = np.ndarray(
        shape, dtype=dtype, buffer=data, offset=offset, order="F" if fortran else "C"
    )
    result.flags.writeable = False
    return result

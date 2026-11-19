"""Bounded parsing of numerical NPY artifacts, without pickle or filesystem access."""

import io
import math
import re
import zipfile
import zlib

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError


def encode_npy(
    array: np.ndarray, *, max_bytes: int = 64 << 20, max_elements: int = 16_777_216
) -> bytes:
    """Encode a bounded plain numerical array without object serialization.

    Byte limits include the NPY header. The caller supplies an already allocated
    array; these limits bound encoding, not upstream algorithm memory.
    """
    for name, limit in (("max_bytes", max_bytes), ("max_elements", max_elements)):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise InputRejectedError(f"NPY {name} must be a positive integer")
    if (
        not isinstance(array, np.ndarray)
        or not 1 <= array.ndim <= 8
        or any(size < 1 for size in array.shape)
        or array.dtype.hasobject
        or array.dtype.fields is not None
        or array.dtype.subdtype is not None
        or array.dtype.kind not in "biuf"
        or array.dtype.itemsize not in (1, 2, 4, 8)
    ):
        raise InputRejectedError("NPY encoding requires a nonempty plain numerical array")
    if array.size > max_elements or array.nbytes > max_bytes:
        raise InputRejectedError("NPY encoding exceeds the numerical allocation ceiling")
    stream = io.BytesIO()
    np.save(stream, array, allow_pickle=False)
    if stream.tell() > max_bytes:
        raise InputRejectedError("NPY encoding including header exceeds its byte ceiling")
    return stream.getvalue()


def decode_npy(
    data: bytes,
    *,
    max_bytes: int = 64 << 20,
    max_elements: int = 16_777_216,
    expected_ndim: int | None = None,
    allow_empty: bool = False,
) -> np.ndarray:
    """Validate the complete header and payload before exposing a read-only array.

    Only plain bool/integer/float arrays in NPY versions 1 and 2 are supported.
    The returned view shares immutable input bytes; parsing never allocates an
    array using an unchecked shape. Callers must separately obtain verified bytes
    through their declared artifact capability.
    """
    if not isinstance(allow_empty, bool):
        raise InputRejectedError("NPY allow_empty must be a boolean")
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
        or any(
            isinstance(n, bool) or not isinstance(n, int) or n < int(not allow_empty) for n in shape
        )
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
    if math.prod(max(1, n) for n in shape) > max_elements or size > max_bytes:
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


def decode_npz_numeric(
    data: bytes,
    *,
    names: frozenset[str],
    max_bytes: int = 64 << 20,
    max_elements: int = 16_777_216,
) -> dict[str, np.ndarray]:
    """Read an exact named numeric NPZ schema with bounded decompression.

    Never extracts files, loads pickle, or trusts ZIP sizes to permit an
    unbounded read. Empty feature arrays are valid diagnostics, not score maps.
    """
    if (
        not isinstance(names, frozenset)
        or not 1 <= len(names) <= 8
        or any(
            not isinstance(n, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", n) for n in names
        )
        or isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < 1
        or isinstance(max_elements, bool)
        or not isinstance(max_elements, int)
        or max_elements < 1
        or not isinstance(data, bytes)
        or len(data) > max_bytes
    ):
        raise InputRejectedError(
            "NPZ needs immutable bounded bytes and an exact numeric member schema"
        )
    result: dict[str, np.ndarray] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            expected = {name + ".npy" for name in names}
            if len(members) != len(expected) or {m.filename for m in members} != expected:
                raise InputRejectedError("NPZ members differ from the declared numeric schema")
            if (
                any(
                    m.flag_bits & 1
                    or m.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or m.file_size < 0
                    for m in members
                )
                or sum(m.file_size for m in members) > max_bytes
            ):
                raise InputRejectedError(
                    "NPZ encryption, compression or expanded size is unsupported"
                )
            remaining = max_bytes
            for member in members:
                with archive.open(member) as stream:
                    payload = stream.read(remaining + 1)
                if len(payload) > remaining or len(payload) != member.file_size:
                    raise InputRejectedError("NPZ member exceeds its expanded-byte ceiling")
                remaining -= len(payload)
                result[member.filename[:-4]] = decode_npy(
                    payload, max_bytes=max_bytes, max_elements=max_elements, allow_empty=True
                )
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError, zlib.error) as error:
        raise InputRejectedError(
            "invalid numeric NPZ archive; preserve the producer artifact"
        ) from error
    return result

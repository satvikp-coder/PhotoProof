"""NumPy image-array conventions and validation for detector plugins.

Every detector receives arrays through ``PluginContext.image`` and works on
them as pure NumPy functions. This module fixes the shared convention:

* Working arrays: ``H x W x C``, C-contiguous, RGB channel order, ``uint8``
  for 8-bit or ``uint16`` for high bit depth; grayscale as ``H x W``.
* Algorithms convert to ``float32`` internally through :func:`to_float32`.
* Arrays handed to plugins are read-only; enforcement helpers live here.

Nothing in this module imports from ``photoproof.core``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

#: Working-array channel order: red, green, blue.
_RGB_CHANNELS = 3

#: Supported dtypes for working arrays.
_UINT_DTYPES = {np.dtype(np.uint8), np.dtype(np.uint16)}

#: BT.601 luma weights for RGB -> grayscale conversion.
_LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float32)


@dataclass(frozen=True)
class ImageSpec:
    """A validated image-array contract."""

    shape: tuple[int, ...]
    dtype: np.dtype
    channels: int | None
    contiguous: bool
    read_only: bool


def validate_image(arr: np.ndarray) -> ImageSpec:
    """Validate an array against the working-image convention.

    Raises :exc:`InputRejectedError` for non-contiguous, object-dtype,
    NaN-containing or zero-size arrays, or for arrays whose shape is not
    ``H x W`` or ``H x W x C``.
    """
    if not isinstance(arr, np.ndarray):
        raise InputRejectedError(
            f"expected a NumPy ndarray, got {type(arr).__name__}; "
            "detectors receive NumPy arrays through PluginContext.image"
        )
    if arr.size == 0:
        raise InputRejectedError("image array is empty; a detector needs at least one pixel")
    if arr.dtype == object or not np.issubdtype(arr.dtype, np.number):
        raise InputRejectedError(
            f"image array has dtype {arr.dtype}; detectors require numeric "
            "uint8 or uint16 pixel arrays"
        )
    if not arr.flags.c_contiguous:
        raise InputRejectedError(
            "image array is not C-contiguous; call "
            "np.ascontiguousarray(arr) before passing it to a detector"
        )
    if np.isnan(arr).any():
        raise InputRejectedError("image array contains NaN values; replace them before detection")

    ndim = arr.ndim
    if ndim == 2:
        channels: int | None = None
    elif ndim == 3:
        channels = arr.shape[2]
    else:
        raise InputRejectedError(
            f"image array has {arr.ndim} dimensions; expected HxW (grayscale) "
            f"or HxWxC (colour); got shape {arr.shape}"
        )

    dtype = np.dtype(arr.dtype)
    if dtype not in _UINT_DTYPES:
        raise InputRejectedError(
            f"image array has dtype {dtype}; detectors require uint8 or "
            f"uint16 pixel arrays, not {dtype}"
        )

    return ImageSpec(
        shape=tuple(arr.shape),
        dtype=dtype,
        channels=channels,
        contiguous=bool(arr.flags.c_contiguous),
        read_only=bool(arr.flags.writeable is False),
    )


def to_float32(arr: np.ndarray) -> np.ndarray:
    """Scale a validated image array to ``float32`` in ``[0, 1]``.

    ``uint8`` values are divided by 255; ``uint16`` values by 65535.
    Returns a new array; the input is unchanged.
    """
    validate_image(arr)
    if arr.dtype == np.uint8:
        return arr.astype(np.float32) / 255.0
    return arr.astype(np.float32) / 65535.0


def to_luma(arr: np.ndarray) -> np.ndarray:
    """Convert an RGB array to single-channel luma using BT.601 weights.

    Returns ``H x W`` as ``float32`` in ``[0, 1]``. Grayscale input is
    returned as-is after :func:`to_float32` scaling.
    """
    spec = validate_image(arr)
    work = to_float32(arr)
    if spec.channels is None:
        return work
    if spec.channels != _RGB_CHANNELS:
        raise InputRejectedError(
            f"to_luma expects 3 colour channels (RGB), got {spec.channels}; "
            "pass a single-channel array directly"
        )
    return np.tensordot(work, _LUMA_WEIGHTS, axes=([2], [0])).astype(np.float32)


def as_read_only(arr: np.ndarray) -> np.ndarray:
    """Return a read-only copy of ``arr``.

    The input is never modified. A copy is always made so the caller
    cannot mutate the returned array's buffer.
    """
    result = np.array(arr, copy=True)
    result.flags.writeable = False
    return result


def ensure_read_only(arr: np.ndarray) -> np.ndarray:
    """Return ``arr`` read-only, copying only if it is currently writeable.

    Use this when handing arrays to plugins: it guarantees callers cannot
    mutate shared pixel data without unnecessary copies.
    """
    if arr.flags.writeable:
        return as_read_only(arr)
    return arr

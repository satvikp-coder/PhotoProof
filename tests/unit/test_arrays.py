"""Tests for the NumPy image-array contract in photoproof.imaging.arrays."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.arrays import (
    ImageSpec,
    as_read_only,
    decoded_to_array,
    ensure_read_only,
    to_float32,
    to_luma,
    validate_image,
    working_to_array,
)
from photoproof.imaging.decoder import DecodedImage
from photoproof.imaging.normalization import WorkingImage


def _rgb_array(h: int = 8, w: int = 8) -> np.ndarray:
    return np.ascontiguousarray(np.zeros((h, w, 3), dtype=np.uint8))


def test_validate_image_accepts_valid_rgb() -> None:
    arr = _rgb_array()
    spec = validate_image(arr)
    assert isinstance(spec, ImageSpec)
    assert spec.shape == (8, 8, 3)
    assert spec.dtype == np.dtype(np.uint8)
    assert spec.channels == 3
    assert spec.contiguous is True
    assert spec.read_only is False


def test_validate_image_accepts_grayscale() -> None:
    arr = np.ascontiguousarray(np.zeros((8, 8), dtype=np.uint8))
    spec = validate_image(arr)
    assert spec.channels is None
    assert spec.shape == (8, 8)


def test_validate_image_accepts_uint16() -> None:
    arr = np.ascontiguousarray(np.zeros((8, 8, 3), dtype=np.uint16))
    spec = validate_image(arr)
    assert spec.dtype == np.dtype(np.uint16)


def test_validate_image_rejects_non_ndarray() -> None:
    with pytest.raises(InputRejectedError, match="expected a NumPy ndarray"):
        validate_image([[0]])  # type: ignore[arg-type]


def test_validate_image_rejects_empty() -> None:
    arr = np.ascontiguousarray(np.zeros((0, 8, 3), dtype=np.uint8))
    with pytest.raises(InputRejectedError, match="empty"):
        validate_image(arr)


def test_validate_image_rejects_object_dtype() -> None:
    arr = np.ascontiguousarray(np.zeros((8, 8, 3), dtype=object))
    with pytest.raises(InputRejectedError, match="dtype"):
        validate_image(arr)


def test_validate_image_rejects_non_contiguous() -> None:
    base = np.zeros((8, 16, 3), dtype=np.uint8)
    arr = base[:, ::2, :]
    with pytest.raises(InputRejectedError, match="not C-contiguous"):
        validate_image(arr)


def test_validate_image_rejects_nan() -> None:
    arr = np.full((8, 8, 3), np.nan, dtype=np.float32)
    arr = np.ascontiguousarray(arr)
    with pytest.raises(InputRejectedError, match="NaN"):
        validate_image(arr)


def test_validate_image_rejects_wrong_ndim() -> None:
    arr = np.ascontiguousarray(np.zeros((8, 8, 8, 3), dtype=np.uint8))
    with pytest.raises(InputRejectedError, match="4 dimensions"):
        validate_image(arr)


def test_validate_image_rejects_float_dtype() -> None:
    arr = np.ascontiguousarray(np.zeros((8, 8, 3), dtype=np.float32))
    with pytest.raises(InputRejectedError, match="uint8 or uint16"):
        validate_image(arr)


def test_to_float32_scales_uint8() -> None:
    arr = np.ascontiguousarray(np.full((2, 2, 3), 128, dtype=np.uint8))
    out = to_float32(arr)
    assert out.dtype == np.float32
    assert np.allclose(out, 128.0 / 255.0)
    # input unchanged
    assert arr.dtype == np.uint8


def test_to_float32_scales_uint16() -> None:
    arr = np.ascontiguousarray(np.full((2, 2, 3), 32768, dtype=np.uint16))
    out = to_float32(arr)
    assert out.dtype == np.float32
    assert np.allclose(out, 32768.0 / 65535.0)


def test_to_float32_rejects_invalid() -> None:
    with pytest.raises(InputRejectedError):
        to_float32(np.full((2, 2), np.nan, dtype=np.float32))


def test_to_luma_rgb() -> None:
    arr = np.ascontiguousarray(np.full((4, 4, 3), [10, 20, 30], dtype=np.uint8))
    out = to_luma(arr)
    assert out.shape == (4, 4)
    assert out.dtype == np.float32
    expected = 0.299 * (10 / 255.0) + 0.587 * (20 / 255.0) + 0.114 * (30 / 255.0)
    assert np.allclose(out, expected)


def test_to_luma_grayscale_passthrough() -> None:
    arr = np.ascontiguousarray(np.full((4, 4), 100, dtype=np.uint8))
    out = to_luma(arr)
    assert out.shape == (4, 4)
    assert np.allclose(out, 100.0 / 255.0)


def test_to_luma_rejects_non_rgb() -> None:
    arr = np.ascontiguousarray(np.zeros((4, 4, 1), dtype=np.uint8))
    with pytest.raises(InputRejectedError, match="3 colour channels"):
        to_luma(arr)


def test_as_read_only_returns_read_only() -> None:
    arr = _rgb_array()
    out = as_read_only(arr)
    assert out.flags.writeable is False
    # input unchanged
    assert arr.flags.writeable is True


def test_ensure_read_only_copies_when_writeable() -> None:
    arr = _rgb_array()
    out = ensure_read_only(arr)
    assert out.flags.writeable is False
    assert arr.flags.writeable is True


def test_ensure_read_only_no_copy_when_already_read_only() -> None:
    arr = as_read_only(_rgb_array())
    out = ensure_read_only(arr)
    assert out is arr


def test_read_only_array_raises_on_mutation() -> None:
    arr = as_read_only(_rgb_array())
    with pytest.raises(ValueError):
        arr[0, 0, 0] = 1


# --------------------------------------------------------------- decoded pixels


def _decoded(
    pixels: bytes,
    *,
    width: int,
    height: int,
    channels: int,
    bit_depth: int = 8,
) -> DecodedImage:
    return DecodedImage(
        source_sha256="a" * 64,
        media_format="png",
        width=width,
        height=height,
        channels=channels,
        bit_depth=bit_depth,
        pixels=pixels,
    )


def test_decoded_to_array_rgb8() -> None:
    decoded = _decoded(bytes(range(12)), width=2, height=2, channels=3)
    arr = decoded_to_array(decoded)
    assert arr.shape == (2, 2, 3)
    assert arr.dtype == np.dtype(np.uint8)
    assert arr[0, 1].tolist() == [3, 4, 5]
    assert arr.flags.writeable is False
    assert arr.flags.c_contiguous is True


def test_decoded_to_array_grayscale_is_two_dimensional() -> None:
    decoded = _decoded(bytes([1, 2, 3, 4]), width=2, height=2, channels=1)
    arr = decoded_to_array(decoded)
    assert arr.shape == (2, 2)
    assert arr[1, 1] == 4


def test_decoded_to_array_high_bit_depth_uses_uint16() -> None:
    pixels = np.array([300, 400, 500, 600], dtype=np.uint16).tobytes()
    decoded = _decoded(pixels, width=2, height=2, channels=1, bit_depth=16)
    arr = decoded_to_array(decoded)
    assert arr.dtype == np.dtype(np.uint16)
    assert arr[0, 0] == 300
    assert arr[1, 1] == 600


def test_decoded_to_array_mutation_raises() -> None:
    arr = decoded_to_array(_decoded(bytes([1, 2, 3, 4]), width=2, height=2, channels=1))
    with pytest.raises(ValueError):
        arr[0, 0] = 9


def test_decoded_to_array_rejects_short_buffer() -> None:
    with pytest.raises(InputRejectedError, match="pixel buffer"):
        decoded_to_array(_decoded(b"\x01\x02", width=2, height=2, channels=3))


def test_decoded_to_array_rejects_invalid_dimensions() -> None:
    with pytest.raises(InputRejectedError, match="invalid dimensions"):
        decoded_to_array(_decoded(b"", width=0, height=2, channels=3))


def _working(pixels: bytes, *, width: int, height: int, dtype: str = "uint8") -> WorkingImage:
    return WorkingImage(
        pixels=pixels,
        mode="RGB",
        width=width,
        height=height,
        colour_space="srgb",
        orientation_applied=False,
        dtype=dtype,  # type: ignore[arg-type]
    )


def test_working_to_array_infers_channels() -> None:
    working = _working(bytes(range(12)), width=2, height=2)
    arr = working_to_array(working)
    assert arr.shape == (2, 2, 3)
    assert arr.dtype == np.dtype(np.uint8)
    assert arr[1, 1].tolist() == [9, 10, 11]
    assert arr.flags.writeable is False


def test_working_to_array_grayscale() -> None:
    arr = working_to_array(_working(bytes([1, 2, 3, 4]), width=2, height=2))
    assert arr.shape == (2, 2)


def test_working_to_array_uint16() -> None:
    pixels = np.array([300, 400, 500, 600], dtype=np.uint16).tobytes()
    arr = working_to_array(_working(pixels, width=2, height=2, dtype="uint16"))
    assert arr.dtype == np.dtype(np.uint16)
    assert arr[0, 0] == 300


def test_working_to_array_rejects_partial_pixels() -> None:
    with pytest.raises(InputRejectedError, match="not a whole"):
        working_to_array(_working(b"\x01\x02\x03", width=2, height=2))

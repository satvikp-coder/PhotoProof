"""Encode already normalized pixels only; never parse original evidence bytes."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError


def encode_preview(pixels: np.ndarray, *, max_edge: int = 2048) -> tuple[bytes, int, int]:
    """Return a deterministic, metadata-free PNG plus dimensions.

    Accept only normalized uint8 L/RGB/RGBA arrays. Alpha is retained.
    High-depth input must pass through working-image normalization first.
    """
    if not 1 <= max_edge <= 4096:
        raise InputRejectedError("preview: max_edge must be 1?4096; choose a bounded display size")
    if (
        not isinstance(pixels, np.ndarray)
        or pixels.dtype != np.uint8
        or pixels.size == 0
        or pixels.ndim not in (2, 3)
        or (pixels.ndim == 3 and pixels.shape[2] not in (3, 4))
    ):
        raise InputRejectedError(
            "preview: expected nonempty uint8 L/RGB/RGBA working pixels; normalize the image first"
        )
    # Image.fromarray starts with no source metadata; caller arrays remain untouched.
    image = Image.fromarray(pixels)
    width, height = image.size
    scale = min(1.0, max_edge / max(width, height))
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue(), image.width, image.height

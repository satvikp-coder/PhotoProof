"""Deterministic metadata-free encoding of already derived visualization pixels."""

import io

import numpy as np
from PIL import Image


def png(pixels: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()

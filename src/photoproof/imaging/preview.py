"""Public preview API for normalized working images and an explicit artifact writer."""

from __future__ import annotations

import numpy as np

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import ArtifactWriter
from photoproof.imaging.normalization import WorkingImage
from photoproof.plugins.support.preview.algorithm import encode_preview


def make_preview(
    working_image: WorkingImage, *, write_artifact: ArtifactWriter, max_edge: int = 2048
) -> ArtifactRef:
    """Store a derived PNG from normalized sRGB pixels, never from evidence bytes.

    Alpha is retained. Callers must label this as derived display content.
    The plugin uses the same encoder and records that label in its manifest outputs.
    """
    if working_image.dtype != "uint8" or working_image.colour_space != "srgb":
        raise InputRejectedError("preview: normalize to 8-bit sRGB before generating a preview")
    channels = {"L": 1, "RGB": 3, "RGBA": 4}.get(working_image.mode)
    if channels is None:
        raise InputRejectedError("preview: unsupported working mode; normalize to L, RGB or RGBA")
    shape: tuple[int, ...] = (working_image.height, working_image.width)
    if channels != 1:
        shape = (*shape, channels)
    try:
        pixels = np.frombuffer(working_image.pixels, dtype=np.uint8).reshape(shape)
    except ValueError as error:
        raise InputRejectedError(
            "preview: working pixel count does not match dimensions"
        ) from error
    data, _, _ = encode_preview(pixels, max_edge=max_edge)
    return write_artifact("derived-preview.png", data, "image/png")

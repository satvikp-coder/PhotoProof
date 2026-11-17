"""Inspect generated PNG chunks, deterministic pixels, bounds and rejection."""

import io
import struct

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.support.preview.algorithm import encode_preview


def _chunks(data):
    offset = 8
    names = []
    while offset < len(data):
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        names.append(data[offset + 4 : offset + 8])
        offset += size + 12
    return names


@pytest.mark.parametrize("shape", [(1, 1), (40, 80, 3), (80, 40, 4)])
def test_metadata_free_deterministic_bounded(shape):
    pixels = np.random.default_rng(10).integers(0, 256, shape, dtype=np.uint8)
    original = pixels.copy()
    pixels.flags.writeable = False
    first, width, height = encode_preview(pixels, max_edge=20)
    assert encode_preview(pixels, max_edge=20)[0] == first
    assert max(width, height) <= 20
    assert set(_chunks(first)) <= {b"IHDR", b"IDAT", b"IEND"}
    with Image.open(io.BytesIO(first)) as image:
        assert not image.info and not image.getexif()
        assert image.size == (width, height)
        assert image.mode == ("L" if len(shape) == 2 else "RGBA" if shape[2] == 4 else "RGB")
    assert np.array_equal(pixels, original)


@pytest.mark.parametrize(
    "pixels",
    [
        np.zeros((1, 1), dtype=np.uint16),
        np.zeros((1, 1), dtype=np.float32),
        np.zeros((0, 1), dtype=np.uint8),
        np.zeros((1, 1, 2), dtype=np.uint8),
        np.zeros((1,), dtype=np.uint8),
    ],
)
def test_reject_non_working_pixels(pixels):
    with pytest.raises(InputRejectedError, match="normalize"):
        encode_preview(pixels)


@pytest.mark.parametrize("edge", [0, 4097])
def test_reject_unbounded_size(edge):
    with pytest.raises(InputRejectedError, match="max_edge"):
        encode_preview(np.zeros((1, 1), dtype=np.uint8), max_edge=edge)


def test_alpha_retained_without_resizing():
    pixels = np.array([[[255, 0, 0, 0], [0, 255, 0, 128]]], dtype=np.uint8)
    data, _, _ = encode_preview(pixels)
    with Image.open(io.BytesIO(data)) as image:
        assert np.array_equal(np.array(image), pixels)


def test_public_api_stores_labelled_artifact():
    import hashlib

    from photoproof.contracts.artifacts import ArtifactRef
    from photoproof.imaging.normalization import WorkingImage
    from photoproof.imaging.preview import make_preview

    working = WorkingImage(
        pixels=bytes([100, 120, 140]),
        mode="RGB",
        width=1,
        height=1,
        colour_space="srgb",
        orientation_applied=False,
    )

    def writer(name, data, media_type):
        return ArtifactRef(
            name=name,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            media_type=media_type,
            producer="support.preview",
        )

    ref = make_preview(working, write_artifact=writer)
    assert ref.name == "derived-preview.png" and ref.media_type == "image/png"


@pytest.mark.parametrize(
    "changes", [{"dtype": "uint16"}, {"colour_space": "source"}, {"mode": "CMYK"}, {"pixels": b""}]
)
def test_public_api_rejects_unnormalized_working_image(changes):
    from photoproof.imaging.normalization import WorkingImage
    from photoproof.imaging.preview import make_preview

    values = dict(
        pixels=bytes([100, 120, 140]),
        mode="RGB",
        width=1,
        height=1,
        colour_space="srgb",
        orientation_applied=False,
    )
    values.update(changes)
    with pytest.raises(InputRejectedError, match="preview"):
        make_preview(
            WorkingImage(**values), write_artifact=lambda *args: pytest.fail("must not write")
        )

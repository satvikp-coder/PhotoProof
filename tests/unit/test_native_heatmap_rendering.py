"""Unknown pixels remain transparent, including when display threshold is zero."""

import io

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.fusion.heatmap.native_renderer import render_native
from photoproof.plugins.fusion.heatmap.schema import Config


def _image(data):
    with Image.open(io.BytesIO(data)) as image:
        assert not image.info
        return np.array(image)


def test_invalid_fill_is_transparent_at_threshold_zero_and_context_stays_derived():
    scores = np.array([[0.9, 0], [0, 0]], np.float32)
    valid = np.array([[True, False], [True, False]])
    preview = np.full((14, 22, 3), 120, np.uint8)
    original = scores.tobytes(), valid.tobytes(), preview.tobytes()
    result = render_native(scores, valid, preview, regions=[], config=Config(display_threshold=0))
    assert result == render_native(
        scores, valid, preview, regions=[], config=Config(display_threshold=0)
    )
    heat, overlay, outlines = (_image(data) for data in result)
    assert np.all(heat[:, :11, 3] == 255)  # Includes an actually measured zero.
    assert not heat[:, 11:, 3].any()  # Unavailable zero fill is not evidence.
    assert np.all(overlay[:, 11:] == 120) and not outlines[..., 3].any()
    assert original == (scores.tobytes(), valid.tobytes(), preview.tobytes())


def test_noninteger_display_scaling_samples_exact_cell_centers():
    scores = np.array([[0.0, 0.5, 1.0]], np.float32)
    valid = np.ones(scores.shape, bool)
    preview = np.full((3, 5, 3), 120, np.uint8)
    heat = _image(
        render_native(scores, valid, preview, regions=[], config=Config(display_threshold=0))[0]
    )
    assert np.array_equal(heat[0, 0], heat[0, 1])
    assert np.array_equal(heat[0, 3], heat[0, 4])
    assert not np.array_equal(heat[0, 1], heat[0, 2])


def test_boundary_outside_fused_grid_is_rejected():
    scores = np.full((2, 2), 0.9, np.float32)
    with pytest.raises(InputRejectedError, match="outside"):
        render_native(
            scores,
            np.ones(scores.shape, bool),
            np.zeros((10, 10, 3), np.uint8),
            regions=[{"polygon": [[0, 0], [2, 0], [2, 2]]}],
        )


def test_nonzero_unknown_fill_is_rejected_before_rendering():
    scores = np.full((2, 2), 0.9, np.float32)
    with pytest.raises(InputRejectedError):
        render_native(
            scores, np.zeros(scores.shape, bool), np.zeros((10, 10, 3), np.uint8), regions=[]
        )

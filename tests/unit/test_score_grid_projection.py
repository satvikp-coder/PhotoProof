"""Spatial evidence remains attached to measured cells and valid support."""

from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.grids import SamplingGrid, project_scores
from photoproof.core.image_geometry import describe_geometry
from photoproof.imaging.decoder import DecodedImage
from photoproof.imaging.normalization import normalize


def _geometry(width, height, orientation=1):
    decoded = DecodedImage(
        source_sha256="a" * 64,
        media_format="png",
        width=width,
        height=height,
        channels=1,
        bit_depth=8,
        pixels=bytes(width * height),
        orientation=orientation,
        mode="L",
    )
    return describe_geometry(decoded, normalize(decoded))


@pytest.mark.parametrize("orientation", range(1, 9))
def test_pixel_grids_follow_every_exif_transform(orientation):
    scores = np.arange(12, dtype=np.float32).reshape(3, 4) / 11
    valid = np.ones(scores.shape, dtype=bool)
    valid[0, 0] = False
    geometry = _geometry(4, 3, orientation)
    grid = SamplingGrid((4, 3), (0, 0), (1, 1), (1, 1))
    values, measured = project_scores(
        scores,
        valid,
        grid=grid,
        geometry=geometry,
        output_shape=tuple(geometry["working_size"][::-1]),
    )
    transforms = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }
    expected = Image.fromarray(np.where(valid, scores, 0))
    expected_mask = Image.fromarray(valid)
    if orientation != 1:
        expected = expected.transpose(transforms[orientation])
        expected_mask = expected_mask.transpose(transforms[orientation])
    np.testing.assert_array_equal(values, np.asarray(expected))
    np.testing.assert_array_equal(measured, np.asarray(expected_mask))


def test_partial_border_cell_has_its_actual_centre_and_support():
    grid = SamplingGrid((5, 2), (0, 0), (3, 2), (3, 2))
    values, valid = project_scores(
        np.array([[0.2, 0.9]], dtype=np.float32),
        np.ones((1, 2), dtype=bool),
        grid=grid,
        geometry=_geometry(5, 2),
        output_shape=(2, 5),
    )
    np.testing.assert_array_equal(values[0], np.array([0.2, 0.2, 0.2, 0.9, 0.9], dtype=np.float32))
    assert valid.all()


def test_gaps_and_invalid_cells_never_become_measured_zero_scores():
    grid = SamplingGrid((6, 2), (1, 0), (3, 2), (2, 2))
    values, valid = project_scores(
        np.array([[0.8, 0.9]], dtype=np.float32),
        np.array([[True, False]]),
        grid=grid,
        geometry=_geometry(6, 2),
        output_shape=(2, 6),
    )
    np.testing.assert_array_equal(valid[0], [False, True, True, False, False, False])
    assert np.all(values[~valid] == 0)  # Fill, explicitly invalid, never a clean measurement.


@pytest.mark.parametrize(
    "field,value",
    [
        ("stride_xy", (0, 1)),
        ("origin_xy", (-1, 0)),
        ("block_xy", (float("nan"), 1)),
        ("origin_xy", (10, 0)),
        ("block_xy", (True, 1)),
    ],
)
def test_invalid_sampling_rejected(field, value):
    grid = replace(SamplingGrid((4, 3), (0, 0), (1, 1), (1, 1)), **{field: value})
    with pytest.raises(InputRejectedError, match="grid"):
        project_scores(
            np.ones((3, 4)),
            np.ones((3, 4), dtype=bool),
            grid=grid,
            geometry=_geometry(4, 3),
            output_shape=(3, 4),
        )


def test_output_limit_checked_before_allocating_output(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("output allocation reached before limit check")

    geometry = _geometry(4, 3)
    monkeypatch.setattr(np, "arange", forbidden)
    with pytest.raises(InputRejectedError, match="ceiling"):
        project_scores(
            np.ones((3, 4)),
            np.ones((3, 4), dtype=bool),
            grid=SamplingGrid((4, 3), (0, 0), (1, 1), (1, 1)),
            geometry=geometry,
            output_shape=(10000, 10000),
        )


@pytest.mark.parametrize(
    "matrix",
    [
        [[1, 1, 0], [0, 1, 0], [0, 0, 1]],
        [[1, 0, 1], [0, 1, 0], [0, 0, 1]],
        [[2**32 + 1, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[1, 0], [0]],
    ],
)
def test_invalid_transform_cannot_shift_or_reinterpret_evidence(matrix):
    geometry = _geometry(4, 3)
    geometry["decoded_to_working"] = matrix
    with pytest.raises(InputRejectedError, match="transform"):
        project_scores(
            np.ones((3, 4)),
            np.ones((3, 4), dtype=bool),
            grid=SamplingGrid((4, 3), (0, 0), (1, 1), (1, 1)),
            geometry=geometry,
            output_shape=(3, 4),
        )


def test_valid_zero_measurement_is_distinct_from_unmeasured_fill():
    values, valid = project_scores(
        np.zeros((1, 1), dtype=np.float32),
        np.ones((1, 1), dtype=bool),
        grid=SamplingGrid((3, 1), (1, 0), (1, 1), (1, 1)),
        geometry=_geometry(3, 1),
        output_shape=(1, 3),
    )
    assert not values.any()
    np.testing.assert_array_equal(valid, [[False, True, False]])

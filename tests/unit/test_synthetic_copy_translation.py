"""A ground-truth fixture must apply the displacement it claims."""

import numpy as np
import pytest

from tests.fixtures.synthetic import copy_move, textured_scene


@pytest.mark.parametrize("translate", [(24, 24), (-24, 0), (0, -24)])
def test_actual_copy_offset_matches_declared_translation(translate):
    image = textured_scene(64, 64, seed=72)
    original = image.copy()
    fixture = copy_move(image, translate=translate)
    sy, sx, ey, ex = fixture.meta["source_bbox"]
    ty, tx, by, bx = fixture.meta["target_bbox"]
    assert (ty - sy, tx - sx) == translate
    np.testing.assert_array_equal(fixture.image[ty:by, tx:bx], image[sy:ey, sx:ex])
    np.testing.assert_array_equal(image, original)
    assert np.count_nonzero(fixture.mask == 1) == (ey - sy) * (ex - sx)
    assert np.count_nonzero(fixture.mask == 2) == (by - ty) * (bx - tx)


def test_identity_copy_has_no_manipulated_ground_truth():
    image = textured_scene(64, 64, seed=72)
    fixture = copy_move(image, translate=(0, 0))
    np.testing.assert_array_equal(fixture.image, image)
    assert fixture.meta["identity"] and not fixture.mask.any()


@pytest.mark.parametrize(
    "params",
    [
        {"translate": (10000, 0)},
        {"translate": (True, 24)},
        {"translate": (1, 1)},
        {"translate": (24, 24), "scale": float("inf")},
        {"translate": (24, 24), "scale": 1e308},
    ],
)
def test_unrepresentable_ground_truth_fails_instead_of_changing_the_requested_transform(params):
    with pytest.raises(ValueError):
        copy_move(textured_scene(64, 64, seed=72), **params)

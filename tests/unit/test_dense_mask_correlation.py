"""Dense correlation mathematical and validity regressions."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.mask.correlation import local_zncc


def test_matches_direct_centered_window() -> None:
    rng = np.random.default_rng(24)
    source = rng.normal(size=(23, 27))
    target = source * 3 + rng.normal(size=source.shape)
    before = source.copy()
    result = local_zncc(source, target, support=np.ones(source.shape, bool))
    for y, x in [(3, 3), (10, 12), (19, 23)]:
        a = source[y - 3 : y + 4, x - 3 : x + 4].ravel()
        b = target[y - 3 : y + 4, x - 3 : x + 4].ravel()
        assert result.valid[y, x]
        assert result.values[y, x] == pytest.approx(np.corrcoef(a, b)[0, 1], abs=1e-6)
    assert not result.valid[:3].any()
    assert not result.valid[:, -3:].any()
    np.testing.assert_array_equal(source, before)
    with pytest.raises(ValueError):
        result.values.setflags(write=True)


def test_flat_and_unsupported_are_unavailable() -> None:
    source = np.arange(441, dtype=np.float64).reshape(21, 21)
    support = np.ones(source.shape, bool)
    support[10, 10] = False
    result = local_zncc(source, source, support=support)
    assert not result.valid[7:14, 7:14].any()
    assert result.valid[3, 3]
    flat = local_zncc(np.ones_like(source), source, support=np.ones(source.shape, bool))
    assert not flat.valid.any()
    assert not flat.values.any()


def test_signed_correlation_and_extreme_finite_values() -> None:
    source = np.random.default_rng(25).uniform(-1, 1, (20, 20)) * 1e300
    result = local_zncc(source, -source, support=np.ones(source.shape, bool))
    np.testing.assert_allclose(result.values[result.valid], -1, atol=1e-6)


@pytest.mark.parametrize("window", [2, 4, 53, True])
def test_invalid_window_rejected(window: int) -> None:
    image = np.ones((10, 10))
    with pytest.raises(InputRejectedError):
        local_zncc(image, image, support=np.ones(image.shape, bool), window=window)


def test_affine_translation_uses_source_pixels_and_clips_border() -> None:
    from photoproof.plugins.copymove.mask.correlation import affine_correlation

    image = np.random.default_rng(26).uniform(size=(80, 100))
    image[35:65, 55:85] = image[10:40, 10:40]
    matrix = np.array([[1.0, 0.0, 45.0], [0.0, 1.0, 25.0]])
    result = affine_correlation(image, matrix, target_box=(35, 55, 65, 85))
    np.testing.assert_allclose(result.values[3:-3, 3:-3], 1, atol=1e-6)
    outside = affine_correlation(image, matrix, target_box=(0, 0, 20, 20))
    assert not outside.valid.any()
    assert not outside.values.any()

"""Synthetic numerical controls, not an image-forgery accuracy benchmark."""

import numpy as np
import pytest
from pydantic import ValidationError

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.plugins.comparison.ssim.algorithm import compare_aligned
from photoproof.plugins.comparison.ssim.schema import Config


def _images(size=64):
    rng = np.random.default_rng(29)
    x = rng.integers(30, 170, (size, size), np.uint8)
    return x, np.ones(x.shape, bool)


def _reference_ssim(x, y, sigma):
    """Independent float64 direct NumPy windows, no OpenCV or production helpers."""
    coordinates = np.arange(-5, 6, dtype=np.float64)
    gaussian = np.exp(-(coordinates**2) / (2 * sigma**2))
    gaussian /= gaussian.sum()
    weights = gaussian[:, None] * gaussian[None, :]
    a = np.lib.stride_tricks.sliding_window_view(x.astype(np.float64) / 255, (11, 11))
    b = np.lib.stride_tricks.sliding_window_view(y.astype(np.float64) / 255, (11, 11))
    ux = (a * weights).sum(axis=(-2, -1))
    uy = (b * weights).sum(axis=(-2, -1))
    vx = ((a - ux[..., None, None]) ** 2 * weights).sum(axis=(-2, -1))
    vy = ((b - uy[..., None, None]) ** 2 * weights).sum(axis=(-2, -1))
    covariance = ((a - ux[..., None, None]) * (b - uy[..., None, None]) * weights).sum(
        axis=(-2, -1)
    )
    return ((2 * ux * uy + 0.0001) * (2 * covariance + 0.0009)) / (
        (ux**2 + uy**2 + 0.0001) * (vx + vy + 0.0009)
    )


@pytest.mark.parametrize("sigma", [0.5, 1.5, 3.0])
def test_matches_independent_gaussian_reference(sigma):
    x, mask = _images()
    y = np.roll(x, 1, axis=1)
    result = compare_aligned(x, y, mask, config=Config(sigma=sigma))
    expected = _reference_ssim(x, y, sigma)
    np.testing.assert_allclose(
        1 - 2 * result.dissimilarity[5:-5, 5:-5], expected, atol=1e-4, rtol=0
    )


@pytest.mark.parametrize("constant", [None, 0, 127, 255])
def test_identical_is_one_and_inputs_unchanged(constant):
    x, mask = _images()
    if constant is not None:
        x.fill(constant)
    original = x.copy()
    result = compare_aligned(x, x, mask)
    assert result.summary["global_ssim"] == pytest.approx(1, abs=1e-6)
    assert np.max(result.dissimilarity) < 1e-6
    assert not result.abs_difference.any()
    np.testing.assert_array_equal(x, original)
    assert not result.valid_mask.flags.writeable
    assert not result.dissimilarity.flags.writeable


def test_local_edit_is_maximum_and_outside_support_unknown():
    x, mask = _images(96)
    y = x.copy()
    y[35:61, 35:61] = 255 - y[35:61, 35:61]
    mask[:10] = False
    result = compare_aligned(x, y, mask)
    row, col = np.unravel_index(np.argmax(result.dissimilarity), x.shape)
    assert 35 <= row < 61 and 35 <= col < 61
    assert not result.valid_mask[:15].any()
    assert not result.dissimilarity[~result.valid_mask].any()
    assert not result.abs_difference[~result.valid_mask].any()
    assert result.dissimilarity.min() >= 0 and result.dissimilarity.max() <= 1


@pytest.mark.parametrize("mode", ["gain_offset", "histogram"])
def test_actual_exposure_normalization_preserves_raw_difference(mode):
    x, mask = _images(96)
    y = x + 35
    raw = compare_aligned(x, y, mask)
    result = compare_aligned(x, y, mask, config=Config(photometric_normalization=mode))
    assert result.dissimilarity[result.valid_mask].mean() < 1e-5
    assert raw.dissimilarity[raw.valid_mask].mean() > 0.01
    np.testing.assert_array_equal(raw.abs_difference, result.abs_difference)
    assert result.summary["normalization"]["applied"]


def test_real_multiscale_and_honest_small_image_unavailability():
    x, mask = _images(256)
    result = compare_aligned(x, x, mask, config=Config(multiscale=True))
    assert result.summary["multiscale"]["value"] == pytest.approx(1, abs=1e-6)
    y = x.copy()
    y[40:200] = 255 - y[40:200]
    changed = compare_aligned(x, y, mask, config=Config(multiscale=True))
    assert changed.summary["multiscale"]["value"] < 0.95
    small, small_mask = _images(32)
    limited = compare_aligned(small, small, small_mask, config=Config(multiscale=True))
    assert limited.summary["multiscale"]["status"] == "unavailable"
    assert limited.summary["global_ssim"] == pytest.approx(1, abs=1e-6)


def test_gain_and_offset_fit_are_real_and_recorded():
    x, mask = _images(96)
    # Keep the known-answer exposure transform inside the uint8 range.
    x = (x // 2).astype(np.uint8)
    y = x * 2 + 7
    result = compare_aligned(x, y, mask, config=Config(photometric_normalization="gain_offset"))
    facts = result.summary["normalization"]
    assert facts["gain"] == pytest.approx(2, abs=1e-5)
    assert facts["offset"] == pytest.approx(7 / 255, abs=1e-6)
    assert result.dissimilarity[result.valid_mask].max() < 1e-5


def test_histogram_normalization_is_not_only_affine():
    x = np.tile(np.arange(64, dtype=np.uint8) * 4, (64, 1))
    y = np.rint((x.astype(np.float64) / 255) ** 2 * 255).astype(np.uint8)
    mask = np.ones(x.shape, bool)
    result = compare_aligned(x, y, mask, config=Config(photometric_normalization="histogram"))
    assert result.dissimilarity[result.valid_mask].max() < 1e-5
    assert len(result.summary["normalization"]["lookup"]) == 256


def test_single_scale_symmetry_without_photometric_fit():
    x, mask = _images()
    y = np.roll(x, 2, axis=0)
    forward = compare_aligned(x, y, mask)
    reverse = compare_aligned(y, x, mask)
    np.testing.assert_allclose(forward.dissimilarity, reverse.dissimilarity, atol=1e-7)


def test_mask_holes_alpha_and_low_overlap_are_not_clean_evidence():
    x, mask = _images(128)
    rgba = np.repeat(x[..., None], 4, axis=2)
    rgba[..., 3] = 255
    rgba[20:30, 20:30, 3] = 0
    mask[:, 32:] = False
    mask[:, :8] = False
    result = compare_aligned(rgba, x, mask)
    assert result.summary["low_confidence"]
    assert not result.valid_mask[15:35, 15:35].any()
    assert not result.valid_mask[:, 27:].any()


def test_edge_erosion_records_suppression_without_erasing_canonical_scores():
    x = np.zeros((64, 64), np.uint8)
    x[:, 32:] = 255
    y = np.roll(x, 1, axis=1)
    mask = np.ones(x.shape, bool)
    raw = compare_aligned(x, y, mask, config=Config(edge_erosion=0))
    result = compare_aligned(x, y, mask)
    assert result.edge_suppressed.any()
    assert np.all(result.valid_mask[result.edge_suppressed])
    np.testing.assert_array_equal(result.dissimilarity, raw.dissimilarity)


@pytest.mark.parametrize("kind", ["float", "shape", "mask", "oversize", "channels"])
def test_bad_inputs_rejected(kind):
    x, mask = _images()
    y = x.copy()
    cfg = Config()
    if kind == "float":
        x = x.astype(np.float32)
    elif kind == "shape":
        y = y[:-1]
    elif kind == "mask":
        mask = mask.astype(np.uint8)
    elif kind == "channels":
        x = np.repeat(x[..., None], 2, axis=2)
    else:
        cfg = Config(max_pixels=121)
    with pytest.raises(InputRejectedError):
        compare_aligned(x, y, mask, config=cfg)


def test_no_fully_measured_window_is_not_applicable():
    x, mask = _images()
    mask[:] = False
    with pytest.raises(NotApplicableError):
        compare_aligned(x, x, mask)


@pytest.mark.parametrize(
    "values",
    [
        {"sigma": float("nan")},
        {"edge_erosion": 3},
        {"max_pixels": 4194305},
        {"photometric_normalization": "fake"},
    ],
)
def test_config_budgets(values):
    with pytest.raises(ValidationError):
        Config(**values)

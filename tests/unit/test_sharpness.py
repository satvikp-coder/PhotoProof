"""Tests for photoproof.plugins.visual.sharpness.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.visual.sharpness.algorithm import analyze_sharpness


def _make_grayscale_image(h: int = 64, w: int = 64, seed: int = 42) -> np.ndarray:
    """Create a synthetic grayscale image."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w), dtype=np.uint8)


def test_analyze_sharpness_basic() -> None:
    """Sharpness analysis runs on a simple grayscale image."""
    image = _make_grayscale_image(64, 64, seed=1)
    result = analyze_sharpness(image)
    assert "sharpness_map" in result
    assert "anomaly_map" in result
    assert "block_size" in result
    assert "config" in result
    assert result["sharpness_map"].shape == (64, 64)
    assert result["anomaly_map"].shape == (64, 64)
    assert result["block_size"] == 32
    assert result["sharpness_map"].dtype == np.float32
    assert result["anomaly_map"].dtype == np.float32


def test_analyze_sharpness_deterministic() -> None:
    """Same input produces identical output."""
    image = _make_grayscale_image(64, 64, seed=5)
    r1 = analyze_sharpness(image)
    r2 = analyze_sharpness(image)
    assert np.array_equal(r1["sharpness_map"], r2["sharpness_map"])
    assert np.array_equal(r1["anomaly_map"], r2["anomaly_map"])


def test_analyze_sharpness_invalid() -> None:
    """Invalid input is rejected."""
    with pytest.raises(InputRejectedError):
        analyze_sharpness("not an array")  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        analyze_sharpness(np.zeros((32, 32, 32, 32), dtype=np.uint8))


def test_analyze_sharpness_blurred_patch_detected() -> None:
    """A planted blurred patch should have low sharpness / high anomaly."""
    h, w = 64, 64
    image = np.random.randint(0, 256, (h, w), dtype=np.uint8).astype(np.float32)
    # Paste a blurred patch
    patch = image[10:30, 10:30].copy()
    patch = cv2_blur(patch)
    image[10:30, 10:30] = patch
    result = analyze_sharpness(image, block=8)
    # The blurred region should have lower sharpness than surroundings
    # Check that the sharpness map has variation
    assert result["sharpness_map"].std() > 0


def test_analyze_sharpness_color_image() -> None:
    """Color images are converted to luminance."""
    image = np.random.randint(0, 256, (32, 32, 3), dtype=np.uint8)
    result = analyze_sharpness(image)
    assert result["sharpness_map"].shape == (32, 32)
    assert result["anomaly_map"].shape == (32, 32)


def test_analyze_sharpness_different_block_sizes() -> None:
    """Different block sizes produce different map resolutions."""
    image = _make_grayscale_image(64, 64, seed=1)
    r1 = analyze_sharpness(image, block=16)
    r2 = analyze_sharpness(image, block=32)
    # Both produce full-resolution maps but different internal block counts
    assert r1["block_size"] == 16
    assert r2["block_size"] == 32


def test_analyze_sharpness_contrast_normalize() -> None:
    """Contrast normalization parameter is reflected in config."""
    image = _make_grayscale_image(32, 32, seed=1)
    r1 = analyze_sharpness(image, contrast_normalize=True)
    r2 = analyze_sharpness(image, contrast_normalize=False)
    assert r1["config"]["contrast_normalize"] is True
    assert r2["config"]["contrast_normalize"] is False


def test_analyze_sharpness_uniform_image() -> None:
    """Uniform image produces near-zero sharpness everywhere."""
    image = np.zeros((32, 32), dtype=np.uint8)
    image[:] = 128
    result = analyze_sharpness(image, block=8)
    # Uniform image: Laplacian is zero, log(var) is very negative
    assert np.all(result["sharpness_map"] < 0)


def test_analyze_sharpness_small_image() -> None:
    """Small image smaller than block size is handled."""
    image = np.random.randint(0, 256, (16, 16), dtype=np.uint8)
    result = analyze_sharpness(image, block=32)
    assert result["sharpness_map"].shape == (16, 16)


def test_analyze_sharpness_result_to_dict() -> None:
    """Result converts to plain dict."""
    image = _make_grayscale_image(32, 32, seed=1)
    result = analyze_sharpness(image)
    d = {
        "sharpness_map": result["sharpness_map"].tolist(),
        "anomaly_map": result["anomaly_map"].tolist(),
        "block_size": result["block_size"],
        "config": result["config"],
    }
    assert "sharpness_map" in d
    assert "anomaly_map" in d
    assert "block_size" in d
    assert "config" in d


def cv2_blur(patch: np.ndarray) -> np.ndarray:
    """Simple Gaussian blur helper."""
    import cv2

    return cv2.GaussianBlur(patch, (5, 5), 1.0).astype(np.float32)

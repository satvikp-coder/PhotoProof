"""Tests for photoproof.plugins.noise.residual.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.noise.residual.algorithm import analyze_residual


def test_analyze_residual_basic() -> None:
    """Residual analysis runs on a simple grayscale array."""
    arr = np.random.randn(64, 64).astype(np.float32) * 10 + 128
    result = analyze_residual(arr)
    assert "residual" in result
    assert "sigma_global" in result
    assert "wavelet" in result
    assert "levels" in result
    assert "method" in result
    assert "channel" in result
    assert result["residual"].shape == (64, 64)
    assert result["residual"].dtype == np.float32
    assert result["sigma_global"] >= 0
    assert result["wavelet"] == "db8"
    assert result["levels"] == 4
    assert result["method"] == "bayesshrink"
    assert result["channel"] == "luma"


def test_analyze_residual_deterministic() -> None:
    """Same input produces identical output."""
    arr = np.random.randn(64, 64).astype(np.float32) * 10 + 128
    r1 = analyze_residual(arr)
    r2 = analyze_residual(arr)
    assert np.array_equal(r1["residual"], r2["residual"])
    assert r1["sigma_global"] == r2["sigma_global"]


def test_analyze_residual_gaussian_noise_sigma() -> None:
    """Residual analysis estimates noise sigma from noisy image."""
    # Create smooth image with known Gaussian noise
    np.random.seed(42)
    base = np.full((128, 128), 128.0, dtype=np.float32)
    true_sigma = 5.0
    noise = np.random.normal(0, true_sigma, base.shape).astype(np.float32)
    noisy = base + noise

    result = analyze_residual(noisy, wavelet="db8", levels=3)
    estimated_sigma = result["sigma_global"]
    # Estimated sigma should be within 50% of true sigma for this test
    assert estimated_sigma > 0
    assert abs(estimated_sigma - true_sigma) < true_sigma * 0.5


def test_analyze_residual_constant_image() -> None:
    """Constant image should have near-zero residual."""
    arr = np.full((64, 64, 3), 128, dtype=np.uint8)
    result = analyze_residual(arr)
    assert np.max(np.abs(result["residual"])) < 10.0


def test_analyze_residual_rgb() -> None:
    """RGB input is converted to luma."""
    arr = np.random.randn(64, 64, 3).astype(np.float32) * 10 + 128
    result = analyze_residual(arr, channel="luma")
    assert result["channel"] == "luma"
    assert result["residual"].shape == (64, 64)


def test_analyze_residual_grayscale() -> None:
    """Grayscale input is used directly."""
    arr = np.random.randn(64, 64).astype(np.float32) * 10 + 128
    result = analyze_residual(arr)
    assert result["channel"] == "luma"
    assert result["residual"].shape == (64, 64)


def test_analyze_residual_invalid() -> None:
    """Invalid input is rejected."""
    with pytest.raises(InputRejectedError):
        analyze_residual(np.array([]))
    with pytest.raises(InputRejectedError):
        analyze_residual("not an array")  # type: ignore[arg-type]


def test_analyze_residual_wiener() -> None:
    """Wiener method works."""
    arr = np.random.randn(64, 64).astype(np.float32) * 10 + 128
    result = analyze_residual(arr, method="wiener")
    assert result["method"] == "wiener"
    assert result["residual"].shape == (64, 64)


def test_analyze_residual_wavelets() -> None:
    """Different wavelets work."""
    arr = np.random.randn(64, 64).astype(np.float32) * 10 + 128
    for wavelet in ["db8", "sym4", "haar"]:
        result = analyze_residual(arr, wavelet=wavelet)
        assert result["wavelet"] == wavelet
        assert result["residual"].shape == (64, 64)


def test_analyze_residual_small_image() -> None:
    """Small image is handled."""
    arr = np.random.randn(16, 16).astype(np.float32) * 10 + 128
    result = analyze_residual(arr, levels=2)
    assert result["residual"].shape == (16, 16)


def test_residual_has_near_zero_mean() -> None:
    """Residual should have near-zero mean for smooth images."""
    arr = np.full((64, 64), 128.0, dtype=np.float32)
    result = analyze_residual(arr)
    assert abs(np.mean(result["residual"])) < 5.0

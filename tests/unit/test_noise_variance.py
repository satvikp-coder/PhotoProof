"""Tests for photoproof.plugins.noise.variance_map.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.noise.variance_map.algorithm import analyze_noise_variance


def test_analyze_noise_variance_basic() -> None:
    """Variance analysis runs on simple arrays."""
    residual = np.random.randn(64, 64).astype(np.float32) * 5.0
    image = np.random.randn(64, 64, 3).astype(np.float32) * 10 + 128
    result = analyze_noise_variance(residual, image)
    assert "sigma_map" in result
    assert "consistency_map" in result
    assert "valid_mask" in result
    assert "valid_fraction" in result
    assert "confidence" in result
    assert result["sigma_map"].shape == (2, 2)  # 64/32 = 2 blocks per dim
    assert result["consistency_map"].shape == (2, 2)
    assert result["valid_mask"].shape == (2, 2)
    assert 0.0 <= result["valid_fraction"] <= 1.0
    assert result["confidence"] in ("high", "medium", "low")


def test_analyze_noise_variance_deterministic() -> None:
    """Same input produces identical output."""
    residual = np.random.randn(64, 64).astype(np.float32) * 5.0
    image = np.random.randn(64, 64, 3).astype(np.float32) * 10 + 128
    r1 = analyze_noise_variance(residual, image)
    r2 = analyze_noise_variance(residual, image)
    assert np.array_equal(r1["sigma_map"], r2["sigma_map"])
    assert np.array_equal(r1["consistency_map"], r2["consistency_map"])
    assert np.array_equal(r1["valid_mask"], r2["valid_mask"])


def test_analyze_noise_variance_planted_region() -> None:
    """Planted high-noise region should have higher sigma values."""
    np.random.seed(42)
    residual = np.zeros((128, 128), dtype=np.float32)
    # Low noise background
    residual[:] = np.random.normal(0, 2.0, (128, 128)).astype(np.float32)
    # High noise region
    residual[40:80, 40:80] = np.random.normal(0, 10.0, (40, 40)).astype(np.float32)
    image = np.full((128, 128, 3), 128.0, dtype=np.float32)

    result = analyze_noise_variance(residual, image, block=16)
    sigma_map = result["sigma_map"]

    # The high-noise region (blocks 2:6, 2:6 in block coordinates) should have
    # higher sigma values than surrounding blocks
    high_noise_region = sigma_map[2:6, 2:6]
    other_mask = np.ones((8, 8), dtype=bool)
    other_mask[2:6, 2:6] = False
    other_blocks = sigma_map[other_mask]

    assert np.mean(high_noise_region) > np.mean(other_blocks)


def test_analyze_noise_variance_invalid() -> None:
    """Invalid input is rejected."""
    residual = np.random.randn(64, 64).astype(np.float32)
    image = np.random.randn(64, 64, 3).astype(np.float32)

    with pytest.raises(InputRejectedError):
        analyze_noise_variance(np.array([]), image)
    with pytest.raises(InputRejectedError):
        analyze_noise_variance(residual, np.array([]))
    with pytest.raises(InputRejectedError):
        analyze_noise_variance("not an array", image)  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        analyze_noise_variance(residual, "not an array")  # type: ignore[arg-type]


def test_analyze_noise_variance_small_blocks() -> None:
    """Small block size works."""
    residual = np.random.randn(64, 64).astype(np.float32) * 5.0
    image = np.random.randn(64, 64, 3).astype(np.float32) * 10 + 128
    result = analyze_noise_variance(residual, image, block=8)
    assert result["sigma_map"].shape == (8, 8)
    assert result["consistency_map"].shape == (8, 8)


def test_analyze_noise_variance_large_block() -> None:
    """Large block size works."""
    residual = np.random.randn(128, 128).astype(np.float32) * 5.0
    image = np.random.randn(128, 128, 3).astype(np.float32) * 10 + 128
    result = analyze_noise_variance(residual, image, block=64)
    assert result["sigma_map"].shape == (2, 2)
    assert result["consistency_map"].shape == (2, 2)


def test_analyze_noise_variance_confidence_levels() -> None:
    """Confidence levels are correctly assigned."""
    # High valid fraction -> high confidence
    residual = np.random.randn(128, 128).astype(np.float32) * 5.0
    image = np.random.randn(128, 128, 3).astype(np.float32) * 10 + 128
    result = analyze_noise_variance(residual, image, block=16)
    assert result["confidence"] == "high"

    # Low valid fraction (saturated image) -> low confidence
    residual = np.random.randn(64, 64).astype(np.float32) * 5.0
    image = np.full((64, 64, 3), 255.0, dtype=np.float32)  # All saturated
    result = analyze_noise_variance(residual, image, block=16)
    assert result["confidence"] == "low"


def test_analyze_noise_variance_flat_image() -> None:
    """Flat image has low noise sigma."""
    residual = np.zeros((64, 64), dtype=np.float32)
    image = np.full((64, 64, 3), 128.0, dtype=np.float32)
    result = analyze_noise_variance(residual, image)
    assert np.max(result["sigma_map"]) < 1.0

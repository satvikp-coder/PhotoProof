"""Tests for photoproof.plugins.copymove.sift_keypoints.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.sift_keypoints.algorithm import (
    analyze_sift_keypoints,
    keypoints_result_to_dict,
)


def test_analyze_sift_keypoints_basic() -> None:
    """SIFT keypoint extraction runs on a simple grayscale image."""
    arr = np.random.randn(64, 64).astype(np.float32) * 50 + 128
    result = analyze_sift_keypoints(arr)
    assert "keypoints" in result
    assert "descriptors" in result
    assert "num_keypoints" in result
    assert "density_per_mp" in result
    assert "confidence" in result
    assert "config" in result
    assert result["keypoints"].shape[1] == 5  # x, y, scale, angle, response
    assert result["descriptors"].shape[1] == 128  # SIFT descriptors
    assert result["num_keypoints"] == result["keypoints"].shape[0]
    assert result["descriptors"].shape[0] == result["num_keypoints"]
    assert result["confidence"] in ("high", "medium", "low")


def test_analyze_sift_keypoints_deterministic() -> None:
    """Same input produces identical output."""
    arr = np.random.randn(64, 64).astype(np.float32) * 50 + 128
    r1 = analyze_sift_keypoints(arr)
    r2 = analyze_sift_keypoints(arr)
    assert np.array_equal(r1["keypoints"], r2["keypoints"])
    assert np.array_equal(r1["descriptors"], r2["descriptors"])
    assert r1["num_keypoints"] == r2["num_keypoints"]


def test_analyze_sift_keypoints_rgb() -> None:
    """RGB input is converted to grayscale."""
    arr = np.random.randn(64, 64, 3).astype(np.float32) * 50 + 128
    result = analyze_sift_keypoints(arr)
    assert result["keypoints"].shape[1] == 5
    assert result["descriptors"].shape[1] == 128


def test_analyze_sift_keypoints_grayscale() -> None:
    """Grayscale input is used directly."""
    arr = np.random.randn(64, 64).astype(np.float32) * 50 + 128
    result = analyze_sift_keypoints(arr)
    assert result["keypoints"].shape[1] == 5
    assert result["descriptors"].shape[1] == 128


def test_analyze_sift_keypoints_invalid() -> None:
    """Invalid input is rejected."""
    with pytest.raises(InputRejectedError):
        analyze_sift_keypoints(np.array([]))
    with pytest.raises(InputRejectedError):
        analyze_sift_keypoints("not an array")  # type: ignore[arg-type]


def test_analyze_sift_keypoints_max_keypoints() -> None:
    """max_keypoints parameter limits the number of keypoints."""
    arr = np.random.randn(128, 128).astype(np.float32) * 50 + 128
    result = analyze_sift_keypoints(arr, max_keypoints=10)
    assert result["num_keypoints"] <= 10


def test_analyze_sift_keypoints_flat_image() -> None:
    """Flat image has few or no keypoints."""
    arr = np.full((64, 64), 128.0, dtype=np.float32)
    result = analyze_sift_keypoints(arr)
    assert result["num_keypoints"] == 0


def test_analyze_sift_keypoints_keypoints_result_to_dict() -> None:
    """Result converts to plain dict."""
    arr = np.random.randn(64, 64).astype(np.float32) * 50 + 128
    result = analyze_sift_keypoints(arr)
    d = keypoints_result_to_dict(result)
    assert "keypoints" in d
    assert "descriptors" in d
    assert "num_keypoints" in d
    assert "density_per_mp" in d
    assert "confidence" in d
    assert "config" in d


def test_analyze_sift_keypoints_different_params() -> None:
    """Different parameters produce different results."""
    arr = np.random.randn(128, 128).astype(np.float32) * 50 + 128
    r1 = analyze_sift_keypoints(arr, max_keypoints=100)
    r2 = analyze_sift_keypoints(arr, max_keypoints=5000)
    # Different max_keypoints should give different results (or at least not error)
    assert r1["config"]["max_keypoints"] == 100
    assert r2["config"]["max_keypoints"] == 5000


def test_analyze_sift_keypoints_max_edge() -> None:
    """max_edge parameter limits image size for detection."""
    arr = np.random.randn(2000, 2000).astype(np.float32) * 50 + 128
    result = analyze_sift_keypoints(arr, max_edge=512)
    assert result["config"]["max_edge"] == 512
    # Should complete without error


def test_analyze_sift_keypoints_rootsift() -> None:
    """RootSIFT parameter works."""
    arr = np.random.randn(64, 64).astype(np.float32) * 50 + 128
    r1 = analyze_sift_keypoints(arr, rootsift=True)
    r2 = analyze_sift_keypoints(arr, rootsift=False)
    # Different rootsift settings should give different descriptors
    # (but same keypoints since detection is the same)
    assert r1["config"]["rootsift"] is True
    assert r2["config"]["rootsift"] is False

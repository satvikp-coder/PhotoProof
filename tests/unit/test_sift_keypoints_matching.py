"""Tests for photoproof.plugins.copymove.matching.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.matching.algorithm import (
    analyze_sift_matching,
    sift_matching_result_to_dict,
)


def _make_keypoints(descriptors_count: int, seed: int = 42) -> np.ndarray:
    """Create synthetic keypoints array."""
    rng = np.random.default_rng(seed)
    n = descriptors_count
    kpts = np.zeros((n, 5), dtype=np.float32)
    kpts[:, 0] = rng.uniform(0, 100, n)  # x
    kpts[:, 1] = rng.uniform(0, 100, n)  # y
    kpts[:, 2] = rng.uniform(1, 5, n)  # scale
    kpts[:, 3] = rng.uniform(0, 360, n)  # angle
    kpts[:, 4] = rng.uniform(10, 100, n)  # response
    return kpts


def _make_descriptors(count: int, seed: int = 42) -> np.ndarray:
    """Create synthetic SIFT descriptors."""
    rng = np.random.default_rng(seed)
    return rng.normal(0, 1, (count, 128)).astype(np.float32)


def test_analyze_sift_matching_basic() -> None:
    """SIFT matching runs on simple descriptors."""
    kpts = _make_keypoints(20, seed=1)
    desc = _make_descriptors(20, seed=2)
    result = analyze_sift_matching(kpts, desc)
    assert "pairs" in result
    assert "distances" in result
    assert "num_keypoints" in result
    assert "num_matches" in result
    assert "matcher_used" in result
    assert "config" in result
    assert result["pairs"].shape[1] == 2
    assert result["distances"].shape[0] == result["pairs"].shape[0]
    assert result["num_keypoints"] == 20
    assert result["matcher_used"] in ("flann", "brute_force")


def test_analyze_sift_matching_deterministic() -> None:
    """Same input produces identical output."""
    kpts = _make_keypoints(30, seed=5)
    desc = _make_descriptors(30, seed=6)
    r1 = analyze_sift_matching(kpts, desc)
    r2 = analyze_sift_matching(kpts, desc)
    assert np.array_equal(r1["pairs"], r2["pairs"])
    assert np.array_equal(r1["distances"], r2["distances"])


def test_analyze_sift_matching_invalid() -> None:
    """Invalid input is rejected."""
    kpts = _make_keypoints(10, seed=1)
    desc = _make_descriptors(10, seed=2)

    with pytest.raises(InputRejectedError):
        analyze_sift_matching(np.array([]), desc)
    with pytest.raises(InputRejectedError):
        analyze_sift_matching(kpts, np.array([]))
    with pytest.raises(InputRejectedError):
        analyze_sift_matching("not an array", desc)  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        analyze_sift_matching(kpts, "not an array")  # type: ignore[arg-type]


def test_analyze_sift_matching_keypoint_shape() -> None:
    """Keypoints must have shape (N, 5)."""
    kpts = np.zeros((10, 3), dtype=np.float32)
    desc = _make_descriptors(10, seed=1)
    with pytest.raises(InputRejectedError):
        analyze_sift_matching(kpts, desc)


def test_analyze_sift_matching_descriptor_shape() -> None:
    """Descriptors must have shape (N, 128)."""
    kpts = _make_keypoints(10, seed=1)
    desc = np.zeros((10, 64), dtype=np.float32)
    with pytest.raises(InputRejectedError):
        analyze_sift_matching(kpts, desc)


def test_analyze_sift_matching_count_mismatch() -> None:
    """Keypoints and descriptors must have same count."""
    kpts = _make_keypoints(10, seed=1)
    desc = _make_descriptors(20, seed=2)
    with pytest.raises(InputRejectedError):
        analyze_sift_matching(kpts, desc)


def test_analyze_sift_matching_single_keypoint() -> None:
    """Single keypoint produces no matches."""
    kpts = _make_keypoints(1, seed=1)
    desc = _make_descriptors(1, seed=2)
    result = analyze_sift_matching(kpts, desc)
    assert result["num_matches"] == 0
    assert result["pairs"].shape[0] == 0


def test_analyze_sift_matching_self_match_excluded() -> None:
    """Self matches are excluded."""
    kpts = _make_keypoints(5, seed=1)
    desc = _make_descriptors(5, seed=2)
    result = analyze_sift_matching(kpts, desc)
    for pair in result["pairs"]:
        assert int(pair[0]) != int(pair[1])


def test_analyze_sift_matching_different_params() -> None:
    """Different parameters produce different configs."""
    kpts = _make_keypoints(30, seed=5)
    desc = _make_descriptors(30, seed=6)
    r1 = analyze_sift_matching(kpts, desc, k=5)
    r2 = analyze_sift_matching(kpts, desc, k=20)
    assert r1["config"]["k"] == 5
    assert r2["config"]["k"] == 20


def test_analyze_sift_matching_symmetric_deduplicated() -> None:
    """Symmetric pairs (i,j) and (j,i) are deduplicated."""
    # Create identical descriptors so they match each other
    desc = np.tile(np.random.randn(1, 128).astype(np.float32), (4, 1))
    kpts = _make_keypoints(4, seed=1)
    result = analyze_sift_matching(kpts, desc, k=4, ratio=1.0)
    pairs = result["pairs"]
    # Check no symmetric duplicates
    seen = set()
    for pair in pairs:
        a, b = int(pair[0]), int(pair[1])
        key = (min(a, b), max(a, b))
        assert key not in seen, f"Symmetric duplicate found: {key}"
        seen.add(key)


def test_analyze_sift_matching_keypoints_result_to_dict() -> None:
    """Result converts to plain dict."""
    kpts = _make_keypoints(10, seed=1)
    desc = _make_descriptors(10, seed=2)
    result = analyze_sift_matching(kpts, desc)
    d = sift_matching_result_to_dict(result)
    assert "pairs" in d
    assert "distances" in d
    assert "num_keypoints" in d
    assert "matcher_used" in d
    assert "config" in d

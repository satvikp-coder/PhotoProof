"""Tests for photoproof.plugins.copymove.clustering.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.clustering.algorithm import (
    analyze_clustering,
    clustering_result_to_dict,
)


def _make_keypoints(n: int, h: int = 100, w: int = 100, seed: int = 42) -> np.ndarray:
    """Create synthetic keypoints array."""
    rng = np.random.default_rng(seed)
    kpts = np.zeros((n, 5), dtype=np.float32)
    kpts[:, 0] = rng.uniform(0, w, n)  # x
    kpts[:, 1] = rng.uniform(0, h, n)  # y
    kpts[:, 2] = rng.uniform(1, 5, n)  # scale
    kpts[:, 3] = rng.uniform(0, 360, n)  # angle
    kpts[:, 4] = rng.uniform(10, 100, n)  # response
    return kpts


def test_analyze_clustering_basic() -> None:
    """Clustering runs on simple keypoints and pairs."""
    kpts = _make_keypoints(20, seed=1)
    pairs = np.array([[0, 1], [2, 3], [4, 5], [6, 7]], dtype=np.int32)
    result = analyze_clustering(kpts, pairs)
    assert "clusters" in result
    assert "num_keypoints" in result
    assert "num_matches" in result
    assert "num_clusters" in result
    assert "method" in result
    assert "config" in result
    assert result["num_keypoints"] == 20
    assert result["num_matches"] == 4
    assert result["method"] == "hierarchical"


def test_analyze_clustering_deterministic() -> None:
    """Same input produces identical output."""
    kpts = _make_keypoints(30, seed=5)
    pairs = np.array([[0, 1], [2, 3], [4, 5]], dtype=np.int32)
    r1 = analyze_clustering(kpts, pairs)
    r2 = analyze_clustering(kpts, pairs)
    assert r1["num_clusters"] == r2["num_clusters"]


def test_analyze_clustering_invalid() -> None:
    """Invalid input is rejected."""
    kpts = _make_keypoints(10, seed=1)
    pairs = np.array([[0, 1]], dtype=np.int32)

    with pytest.raises(InputRejectedError):
        analyze_clustering(np.array([]), pairs)
    with pytest.raises(InputRejectedError):
        analyze_clustering(kpts, np.array([]))
    with pytest.raises(InputRejectedError):
        analyze_clustering("not an array", pairs)  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        analyze_clustering(kpts, "not an array")  # type: ignore[arg-type]


def test_analyze_clustering_planted_translation() -> None:
    """Planted translation copy should yield one cluster."""
    np.random.seed(42)
    # Create keypoints with a clear translation pattern
    kpts = []
    pairs = []
    # Source points in top-left
    for i in range(10):
        kpts.append([i * 5 + 10, i * 5 + 10, 5.0, 0.0, 50.0])
    # Target points shifted by (50, 50)
    for i in range(10):
        kpts.append([i * 5 + 60, i * 5 + 60, 5.0, 0.0, 50.0])
    # Pairs connecting source to target
    for i in range(10):
        pairs.append([i, 10 + i])
    kpts = np.array(kpts, dtype=np.float32)
    pairs = np.array(pairs, dtype=np.int32)
    result = analyze_clustering(kpts, pairs)
    assert result["num_clusters"] >= 1


def test_analyze_clustering_empty() -> None:
    """Empty input is handled."""
    kpts = np.zeros((0, 5), dtype=np.float32)
    pairs = np.zeros((0, 2), dtype=np.int32)
    result = analyze_clustering(kpts, pairs)
    assert result["num_keypoints"] == 0
    assert result["num_matches"] == 0
    assert result["num_clusters"] == 0
    assert result["clusters"] == []


def test_analyze_clustering_clustering_result_to_dict() -> None:
    """Result converts to plain dict."""
    kpts = _make_keypoints(64, seed=1)
    pairs = np.array([[0, 1], [2, 3]], dtype=np.int32)
    result = analyze_clustering(kpts, pairs)
    d = clustering_result_to_dict(result)
    assert "clusters" in d
    assert "num_keypoints" in d
    assert "num_matches" in d
    assert "method" in d
    assert "config" in d


def test_analyze_clustering_methods() -> None:
    """Both hierarchical and dbscan methods work."""
    kpts = _make_keypoints(30, seed=1)
    pairs = np.array([[i, i + 1] for i in range(0, 28, 2)], dtype=np.int32)
    for method in ["hierarchical", "dbscan"]:
        result = analyze_clustering(kpts, pairs, method=method)
        assert result["method"] == method
        assert isinstance(result["clusters"], list)


def test_analyze_clustering_min_matches_filter() -> None:
    """Clusters below min_matches_per_cluster are filtered out."""
    kpts = np.random.randn(20, 5).astype(np.float32) * 50 + 100
    pairs = np.array([[0, 1], [2, 3]], dtype=np.int32)  # Only 2 pairs total
    result = analyze_clustering(kpts, pairs, min_matches_per_cluster=4)
    # With only 2 pairs total and min_matches=4, no cluster should form
    assert result["num_clusters"] == 0


def test_analyze_clustering_invalid_shapes() -> None:
    """Invalid keypoints or pairs shape is rejected."""
    kpts_bad = np.zeros((10, 3), dtype=np.float32)
    pairs = np.array([[0, 1]], dtype=np.int32)
    with pytest.raises(InputRejectedError):
        analyze_clustering(kpts_bad, pairs)

    kpts_good = _make_keypoints(10, seed=1)
    pairs_bad = np.zeros((5, 3), dtype=np.int32)
    with pytest.raises(InputRejectedError):
        analyze_clustering(kpts_good, pairs_bad)

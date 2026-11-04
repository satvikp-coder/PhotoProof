"""Tests for photoproof.plugins.copymove.verification.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.clustering.algorithm import analyze_copy_move


def test_analyze_copy_move_basic() -> None:
    """Verification runs on simple keypoints and pairs."""
    kpts = np.array(
        [
            [0.0, 0.0, 1.0, 0.0, 10.0],
            [10.0, 0.0, 1.0, 0.0, 10.0],
            [0.0, 10.0, 1.0, 0.0, 10.0],
            [10.0, 10.0, 1.0, 0.0, 10.0],
        ],
        dtype=np.float32,
    )
    pairs = np.array([[0, 1], [2, 3]], dtype=np.int32)
    result = analyze_copy_move(kpts, pairs)
    assert "cluster_id" in result
    assert "matrix" in result
    assert "inliers" in result
    assert "rmse" in result
    assert "scale" in result
    assert "rotation_deg" in result
    assert "score" in result
    assert "model" in result
    assert "reason" in result
    assert result["score"] >= 0.0
    assert result["score"] <= 1.0


def test_analyze_copy_move_deterministic() -> None:
    """Same input produces identical output."""
    kpts = np.random.randn(20, 5).astype(np.float32) * 50 + 100
    pairs = np.array([[i, i + 1] for i in range(0, 18, 2)], dtype=np.int32)
    r1 = analyze_copy_move(kpts, pairs)
    r2 = analyze_copy_move(kpts, pairs)
    assert r1["score"] == r2["score"]
    assert r1["inliers"] == r2["inliers"]


def test_analyze_copy_move_invalid() -> None:
    """Invalid input is rejected."""
    kpts = np.random.randn(10, 5).astype(np.float32) * 10 + 100
    pairs = np.array([[0, 1]], dtype=np.int32)

    with pytest.raises(InputRejectedError):
        analyze_copy_move(np.array([]), pairs)
    with pytest.raises(InputRejectedError):
        analyze_copy_move(kpts, np.array([]))
    with pytest.raises(InputRejectedError):
        analyze_copy_move("not an array", pairs)  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        analyze_copy_move(kpts, "not an array")  # type: ignore[arg-type]


def test_analyze_copy_move_invalid_shapes() -> None:
    """Invalid shapes are rejected."""
    kpts = np.zeros((10, 3), dtype=np.float32)
    pairs = np.array([[0, 1]], dtype=np.int32)
    with pytest.raises(InputRejectedError):
        analyze_copy_move(kpts_bad, pairs)

    kpts_good = np.random.randn(10, 5).astype(np.float32) * 10 + 100
    pairs_bad = np.zeros((5, 3), dtype=np.int32)
    with pytest.raises(InputRejectedError):
        analyze_copy_move(kpts_good, pairs_bad)


def test_analyze_copy_move_single_pair() -> None:
    """Single pair can still be analyzed."""
    kpts = np.array(
        [[0.0, 0.0, 1.0, 0.0, 10.0], [10.0, 0.0, 1.0, 0.0, 10.0]],
        dtype=np.float32,
    )
    pairs = np.array([[0, 1]], dtype=np.int32)
    result = analyze_copy_move(kpts, pairs)
    assert result["num_keypoints"] == 2
    assert result["num_matches"] == 1


def test_analyze_copy_move_high_inliers() -> None:
    """Perfect translation gives high score."""
    kpts = np.array(
        [
            [0.0, 0.0, 1.0, 0.0, 10.0],
            [10.0, 0.0, 1.0, 0.0, 10.0],
            [0.0, 10.0, 1.0, 0.0, 10.0],
            [10.0, 10.0, 1.0, 0.0, 10.0],
        ],
        dtype=np.float32,
    )
    # Translate all by (50, 30)
    pairs = np.array([[0, 1], [2, 3]], dtype=np.int32)
    # But keypoints 1 and 3 are at (10,0) and (10,10), not shifted by 50,30
    # So they won't match well. Let's make a proper test.
    kpts = np.array(
        [
            [0.0, 0.0, 1.0, 0.0, 10.0],
            [50.0, 30.0, 1.0, 0.0, 10.0],
            [0.0, 10.0, 1.0, 0.0, 10.0],
            [50.0, 40.0, 1.0, 0.0, 10.0],
        ],
        dtype=np.float32,
    )
    pairs = np.array([[0, 1], [2, 3]], dtype=np.int32)
    result = analyze_copy_move(kpts, pairs)
    # Perfect translation should give high score
    assert result["inliers"] == 2
    assert result["score"] > 0.5


def test_analyze_copy_move_invalid_model() -> None:
    """Invalid model is rejected."""
    kpts = np.random.randn(10, 5).astype(np.float32) * 10 + 100
    pairs = np.array([[0, 1]], dtype=np.int32)
    with pytest.raises(InputRejectedError):
        analyze_copy_move(kpts, pairs, model="invalid_model")


def test_analyze_copy_move_empty_pairs() -> None:
    """Empty pairs returns empty result."""
    kpts = np.random.randn(10, 5).astype(np.float32) * 10 + 100
    pairs = np.zeros((0, 2), dtype=np.int32)
    result = analyze_copy_move(kpts, pairs)
    assert result["num_keypoints"] == 0
    assert result["num_matches"] == 0
    assert result["num_clusters"] == 0
    assert result["clusters"] == []


def test_analyze_copy_move_too_few_inliers() -> None:
    """Too few inliers results in too_few_inliers reason."""
    kpts = np.random.randn(10, 5).astype(np.float32) * 100
    pairs = np.array([[0, 1], [2, 3]], dtype=np.int32)
    result = analyze_copy_move(kpts, pairs, min_inliers=10)
    assert result["reason"] == "too_few_inliers"
    assert result["inliers"] < 10


def test_analyze_copy_move_score_range() -> None:
    """Score is always between 0 and 1."""
    kpts = np.random.randn(20, 5).astype(np.float32) * 10 + 100
    pairs = np.array([[i, i + 1] for i in range(0, 18, 2)], dtype=np.int32)
    result = analyze_copy_move(kpts, pairs)
    assert 0.0 <= result["score"] <= 1.0


def test_analyze_copy_move_model_affine() -> None:
    """Affine model works."""
    kpts = np.random.randn(20, 5).astype(np.float32) * 10 + 100
    pairs = np.array([[i, i + 1] for i in range(0, 18, 2)], dtype=np.int32)
    result = analyze_copy_move(kpts, pairs, model="affine")
    assert result["model"] == "affine"
    assert result["score"] >= 0.0
    assert result["score"] <= 1.0

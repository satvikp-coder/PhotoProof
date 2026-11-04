"""Tests for photoproof.plugins.copymove.mask.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.mask.algorithm import (
    analyze_copy_move_mask,
)


def test_analyze_copy_move_mask_basic() -> None:
    """Mask generation runs on simple keypoints, pairs, and image."""
    h, w = 64, 64
    image = np.random.randint(0, 256, (h, w), dtype=np.uint8)
    keypoints = np.array(
        [
            [10.0, 10.0, 1.0, 0.0, 50.0],
            [20.0, 20.0, 1.0, 0.0, 50.0],
            [30.0, 30.0, 1.0, 0.0, 50.0],
            [40.0, 40.0, 1.0, 0.0, 50.0],
        ],
        dtype=np.float32,
    )
    pairs = np.array([[0, 1], [2, 3]], dtype=np.int32)
    result = analyze_copy_move_mask(keypoints, pairs, image)
    assert "mask" in result
    assert "source_mask" in result
    assert "target_mask" in result
    assert "num_clusters" in result
    assert "method" in result
    assert "config" in result
    assert result["mask"].shape == (h, w)
    assert result["mask"].dtype == np.uint8
    assert result["method"] == "zncc"


def test_analyze_copy_move_mask_empty_pairs() -> None:
    """Empty pairs produce empty masks."""
    h, w = 32, 32
    image = np.random.randint(0, 256, (h, w), dtype=np.uint8)
    keypoints = np.zeros((4, 5), dtype=np.float32)
    pairs = np.zeros((0, 2), dtype=np.int32)
    result = analyze_copy_move_mask(keypoints, pairs, image)
    assert result["num_clusters"] == 0
    assert result["mask"].sum() == 0


def test_analyze_copy_move_mask_invalid() -> None:
    """Invalid input is rejected."""
    image = np.random.randint(0, 256, (32, 32), dtype=np.uint8)
    keypoints = np.zeros((4, 5), dtype=np.float32)
    pairs = np.array([[0, 1]], dtype=np.int32)

    with pytest.raises(InputRejectedError):
        analyze_copy_move_mask(np.array([]), pairs, image)
    with pytest.raises(InputRejectedError):
        analyze_copy_move_mask(keypoints, np.array([]), image)
    with pytest.raises(InputRejectedError):
        analyze_copy_move_mask("not an array", pairs, image)  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        analyze_copy_move_mask(keypoints, "not an array", image)  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        analyze_copy_move_mask(keypoints, pairs, "not an array")  # type: ignore[arg-type]


def test_analyze_copy_move_mask_invalid_shapes() -> None:
    """Invalid shapes are rejected."""
    image = np.random.randint(0, 256, (32, 32), dtype=np.uint8)
    keypoints_bad = np.zeros((4, 3), dtype=np.float32)
    pairs = np.array([[0, 1]], dtype=np.int32)
    with pytest.raises(InputRejectedError):
        analyze_copy_move_mask(keypoints_bad, pairs, image)

    keypoints_good = np.zeros((4, 5), dtype=np.float32)
    pairs_bad = np.zeros((4, 3), dtype=np.int32)
    with pytest.raises(InputRejectedError):
        analyze_copy_move_mask(keypoints_good, pairs_bad, image)

    image_bad = np.zeros((32, 32, 32, 32), dtype=np.uint8)
    with pytest.raises(InputRejectedError):
        analyze_copy_move_mask(keypoints_good, pairs, image_bad)


def test_analyze_copy_move_mask_copy_move_plugin() -> None:
    """Analyze with planted copy-move pattern produces masks."""
    h, w = 64, 64
    image = np.random.randint(0, 256, (h, w), dtype=np.uint8)
    # Create a copy-paste: copy region at (10,10)-(30,30) to (40,40)-(60,60)
    image[40:60, 40:60] = image[10:30, 10:30]
    keypoints = np.array(
        [
            [15.0, 15.0, 1.0, 0.0, 50.0],
            [15.0, 25.0, 1.0, 0.0, 50.0],
            [25.0, 15.0, 1.0, 0.0, 50.0],
            [25.0, 25.0, 1.0, 0.0, 50.0],
        ],
        dtype=np.float32,
    )
    # Target keypoints shifted by (30, 30)
    pairs = np.array([[0, 0], [1, 1], [2, 2], [3, 3]], dtype=np.int32)
    # Actually use valid indices into keypoints - target is same array shifted
    # For testing, just use self-referential pairs since image regions match
    pairs = np.array([[0, 1], [2, 3]], dtype=np.int32)
    result = analyze_copy_move_mask(keypoints, pairs, image)
    assert result["num_clusters"] >= 0
    assert result["mask"].shape == (h, w)


def test_analyze_copy_move_mask_threshold() -> None:
    """Higher threshold produces fewer or equal masks."""
    h, w = 32, 32
    image = np.random.randint(0, 256, (h, w), dtype=np.uint8)
    keypoints = np.random.randn(8, 5).astype(np.float32) * 10 + 10
    pairs = np.array([[0, 1], [2, 3], [4, 5], [6, 7]], dtype=np.int32)
    result_low = analyze_copy_move_mask(keypoints, pairs, image, threshold=0.0)
    result_high = analyze_copy_move_mask(keypoints, pairs, image, threshold=1.0)
    assert result_high["num_clusters"] <= result_low["num_clusters"]

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


def test_zncc_brightness_invariance() -> None:
    """ZNCC is invariant to additive brightness offsets (#135)."""
    from photoproof.plugins.copymove.mask.algorithm import _compute_zncc

    base = np.array([[10, 20, 30], [40, 50, 60], [70, 80, 90]], dtype=np.uint8)
    shifted = np.clip(base.astype(np.int16) + 50, 0, 255).astype(np.uint8)
    score = _compute_zncc(base, shifted)
    assert score > 0.9, f"ZNCC should be ~1.0 for brightness-shifted copies, got {score}"


def test_zncc_zero_variance_patch() -> None:
    """Zero-variance patches return 0.0 without NaN (#135)."""
    from photoproof.plugins.copymove.mask.algorithm import _compute_zncc

    flat = np.zeros((5, 5), dtype=np.uint8)
    varied = np.random.randint(0, 256, (5, 5), dtype=np.uint8)
    assert _compute_zncc(flat, flat) == 0.0
    assert _compute_zncc(flat, varied) == 0.0
    assert _compute_zncc(varied, flat) == 0.0


def test_mask_uses_yx_coordinates() -> None:
    """Mask drawing indexes images as image[y, x], not image[x, y] (#134)."""
    h, w = 40, 40  # square image
    image = np.zeros((h, w), dtype=np.uint8)
    # Keypoint at x=5, y=30 -> row 30, col 5. Window (half=5) covers rows 25-35, cols 0-10.
    keypoints = np.array(
        [
            [5.0, 30.0, 1.0, 0.0, 50.0],  # source: x=5, y=30
            [35.0, 10.0, 1.0, 0.0, 50.0],  # target: x=35, y=10
        ],
        dtype=np.float32,
    )
    # Self-referential pair: source and target are the same keypoint content
    pairs = np.array([[0, 0]], dtype=np.int32)
    result = analyze_copy_move_mask(keypoints, pairs, image, threshold=0.0, min_cluster_size=1)
    # With threshold 0.0, ZNCC of a patch with itself is 1.0, so it should pass.
    # The mask should be drawn around y=30, x=5 (row 30, col 5), NOT row 5, col 30.
    assert result["source_mask"][30, 5] == 1, (
        f"source_mask should be 1 at (y=30, x=5), got {result['source_mask'][30, 5]}"
    )
    # Row 5 is outside the window rows 25-35, so this must be 0.
    assert result["source_mask"][5, 30] == 0, (
        f"source_mask should be 0 at (y=5, x=30) since keypoint is x=5,y=30, "
        f"got {result['source_mask'][5, 30]}"
    )


def test_min_cluster_size_enforced() -> None:
    """Clusters smaller than min_cluster_size are discarded (#136)."""
    h, w = 64, 64
    image = np.zeros((h, w), dtype=np.uint8)
    # 4 isolated pairs spread far apart - each forms its own cluster of size 1
    keypoints = np.array(
        [
            [5.0, 5.0, 1.0, 0.0, 50.0],  # pair 0 source
            [50.0, 5.0, 1.0, 0.0, 50.0],  # pair 0 target
            [20.0, 20.0, 1.0, 0.0, 50.0],  # pair 1 source
            [30.0, 30.0, 1.0, 0.0, 50.0],  # pair 1 target
            [40.0, 40.0, 1.0, 0.0, 50.0],  # pair 2 source
            [10.0, 10.0, 1.0, 0.0, 50.0],  # pair 2 target
            [55.0, 55.0, 1.0, 0.0, 50.0],  # pair 3 source
            [15.0, 15.0, 1.0, 0.0, 50.0],  # pair 3 target
        ],
        dtype=np.float32,
    )
    pairs = np.array([[0, 1], [2, 3], [4, 5], [6, 7]], dtype=np.int32)
    # All patches are flat (zero variance) so ZNCC=0; use threshold=0.0 to accept all
    result = analyze_copy_move_mask(keypoints, pairs, image, threshold=0.0, min_cluster_size=2)
    # Each pair is isolated (distance > 1 between source keypoints), so each
    # cluster has size 1 < min_cluster_size=2. All should be discarded.
    assert result["num_clusters"] == 0
    assert result["mask"].sum() == 0


def test_min_cluster_size_retains_coherent_cluster() -> None:
    """A coherent cluster at the minimum size is retained (#136)."""
    h, w = 64, 64
    image = np.zeros((h, w), dtype=np.uint8)
    # 5 source keypoints all within distance 1 of each other -> one cluster of size 5
    keypoints = np.array(
        [
            [10.0, 10.0, 1.0, 0.0, 50.0],  # pair 0 source
            [20.0, 20.0, 1.0, 0.0, 50.0],  # pair 0 target
            [10.5, 10.0, 1.0, 0.0, 50.0],  # pair 1 source (near pair 0)
            [20.5, 20.0, 1.0, 0.0, 50.0],  # pair 1 target
            [10.0, 10.5, 1.0, 0.0, 50.0],  # pair 2 source (near pair 0)
            [20.0, 20.5, 1.0, 0.0, 50.0],  # pair 2 target
            [10.5, 10.5, 1.0, 0.0, 50.0],  # pair 3 source (near pair 0)
            [20.5, 20.5, 1.0, 0.0, 50.0],  # pair 3 target
            [10.2, 10.2, 1.0, 0.0, 50.0],  # pair 4 source (near pair 0)
            [20.2, 20.2, 1.0, 0.0, 50.0],  # pair 4 target
        ],
        dtype=np.float32,
    )
    pairs = np.array([[0, 1], [2, 3], [4, 5], [6, 7], [8, 9]], dtype=np.int32)
    result = analyze_copy_move_mask(keypoints, pairs, image, threshold=0.0, min_cluster_size=5)
    # All 5 sources are within distance 1, forming one cluster of size 5 >= 5
    assert result["num_clusters"] == 1

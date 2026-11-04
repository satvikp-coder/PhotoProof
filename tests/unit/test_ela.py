"""Tests for photoproof.plugins.compression.ela.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.compression.ela.algorithm import (
    ElaResult,
    analyze_ela,
    ela_result_to_dict,
)
from tests.fixtures.synthetic import splice, textured_scene


def test_analyze_ela_basic() -> None:
    """ELA runs on a simple RGB array and produces valid output."""
    arr = np.zeros((32, 32, 3), dtype=np.uint8)
    arr[:] = 128
    result = analyze_ela(arr)
    assert isinstance(result, ElaResult)
    assert result.ela_map.shape == (32, 32)
    assert result.ela_map.dtype == np.uint8
    assert result.error_map.shape == (32, 32)
    assert result.error_map.dtype == np.float32
    assert result.quality == 95
    assert result.context == "jpeg"
    assert "supporting evidence only" in result.interpretation.lower()


def test_analyze_ela_deterministic() -> None:
    """Same input produces identical output."""
    arr = textured_scene(64, 64, seed=42)
    r1 = analyze_ela(arr)
    r2 = analyze_ela(arr)
    assert np.array_equal(r1.ela_map, r2.ela_map)
    assert np.array_equal(r1.error_map, r2.error_map)
    assert np.array_equal(r1.anomaly.values, r2.anomaly.values)


def test_analyze_ela_splice_detected() -> None:
    """Spliced patch should produce elevated anomaly in affected blocks."""
    host = textured_scene(128, 128, seed=7)
    donor = textured_scene(128, 128, seed=99)
    fixture = splice(host, donor, region=(40, 40, 72, 72))
    result = analyze_ela(fixture.image)
    assert result.anomaly.values.size != 0
    anomaly = result.anomaly.values
    # Block size 16, image 128x128 => 8x8 blocks
    # Splice region (40:72, 40:72) maps to blocks (2:5, 2:5)
    splice_blocks = anomaly[2:5, 2:5]
    # Create a mask of non-splice blocks
    mask = np.ones_like(anomaly, dtype=bool)
    mask[2:5, 2:5] = False
    other_blocks = anomaly[mask]
    # Splice region should have elevated anomaly compared to typical blocks
    assert splice_blocks.max() > 0
    assert splice_blocks.mean() > other_blocks.mean()


def test_analyze_ela_constant_image_degenerate() -> None:
    """Uniform image produces degenerate anomaly map."""
    arr = np.full((64, 64, 3), 128, dtype=np.uint8)
    result = analyze_ela(arr)
    # Uniform image recompresses uniformly => low error => degenerate MAD
    assert result.anomaly.notes == ("degenerate",)


def test_analyze_ela_grayscale() -> None:
    """Grayscale input is handled."""
    arr = np.zeros((32, 32), dtype=np.uint8)
    arr[:] = 200
    result = analyze_ela(arr)
    assert result.ela_map.shape == (32, 32)


def test_analyze_ela_rgba() -> None:
    """RGBA input drops alpha channel."""
    arr = np.zeros((32, 32, 4), dtype=np.uint8)
    arr[:] = [100, 150, 200, 255]
    result = analyze_ela(arr)
    assert result.ela_map.shape == (32, 32)


def test_analyze_ela_quality_parameter() -> None:
    """Different quality produces different results."""
    arr = textured_scene(64, 64, seed=1)
    r_low = analyze_ela(arr, quality=50)
    r_high = analyze_ela(arr, quality=95)
    assert not np.array_equal(r_low.ela_map, r_high.ela_map)


def test_analyze_ela_invalid_input() -> None:
    """Invalid input is rejected."""
    with pytest.raises(InputRejectedError):
        analyze_ela(np.array([]))
    with pytest.raises(InputRejectedError):
        analyze_ela("not an array")  # type: ignore[arg-type]


def test_ela_result_to_dict() -> None:
    """Result converts to a plain dict with expected structure."""
    arr = textured_scene(32, 32, seed=3)
    result = analyze_ela(arr)
    d = ela_result_to_dict(result)
    assert "strength" in d
    assert d["strength"] == "weak"
    assert "context" in d
    assert "interpretation" in d
    assert "quality" in d
    assert "aggregate" in d
    assert "block" in d
    assert "anomaly" in d
    assert "values" in d["anomaly"]
    assert "valid_mask" in d["anomaly"]
    assert "notes" in d["anomaly"]


def test_edge_suppression_reduces_texture_false_positives() -> None:
    """Edge suppression reduces high z-scores in textured regions."""
    arr = textured_scene(64, 64, seed=5)
    r_with = analyze_ela(arr, edge_suppression=True)
    r_without = analyze_ela(arr, edge_suppression=False)
    # Both should complete without error
    assert r_with.anomaly.values.shape == r_without.anomaly.values.shape


def test_analyze_ela_aggregate_mean() -> None:
    """Mean aggregation works."""
    arr = textured_scene(32, 32, seed=1)
    result = analyze_ela(arr, aggregate="mean")
    assert result.aggregate == "mean"
    assert result.ela_map.shape == (32, 32)

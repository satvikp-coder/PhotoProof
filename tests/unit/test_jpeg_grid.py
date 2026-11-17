"""Tests for photoproof.plugins.compression.jpeg_grid.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.compression.jpeg_grid.algorithm import (
    _compute_blocking_energy,
    _compute_phase_energy_map,
    _find_dominant_phase,
    _second_order_difference,
    analyze_jpeg_grid,
    grid_result_to_dict,
)


def test_second_order_difference_vertical() -> None:
    """Vertical second difference should detect edges."""
    arr = np.zeros((10, 10), dtype=np.float32)
    arr[3:7, :] = 100.0  # Step edge
    d2 = _second_order_difference(arr, axis=0)
    # Second difference should be non-zero at the edge boundaries
    assert np.abs(d2[2, 0]) > 0 or np.abs(d2[6, 0]) > 0


def test_second_order_difference_horizontal() -> None:
    """Horizontal second difference should detect vertical edges."""
    arr = np.zeros((10, 10), dtype=np.float32)
    arr[:, 3:7] = 100.0  # Vertical step edge
    d2 = _second_order_difference(arr, axis=1)
    assert np.abs(d2[0, 2]) > 0 or np.abs(d2[0, 6]) > 0


def test_blocking_energy_grid_aligned() -> None:
    """Grid-aligned second differences should produce high energy."""
    d2 = np.zeros((16, 16), dtype=np.float32)
    # Put strong energy at grid lines x ≡ 2 (mod 8)
    d2[:, 2::8] = 100.0
    energy = _compute_blocking_energy(d2, dx=2, dy=0)
    assert energy[:, 2].sum() > 0


def test_phase_energy_map() -> None:
    """Phase energy map should have shape (8, 8, h, w)."""
    d2 = np.random.randn(32, 32).astype(np.float32)
    energy_maps, total = _compute_phase_energy_map(d2)
    assert energy_maps.shape == (8, 8, 32, 32)
    assert total.shape == (32, 32)


def test_find_dominant_phase() -> None:
    """Dominant phase should be found correctly."""
    energy_maps = np.zeros((8, 8, 16, 16), dtype=np.float32)
    # Put high energy at phase (3, 5)
    energy_maps[3, 5, :, :] = 100.0
    dx, dy, confidence = _find_dominant_phase(energy_maps)
    assert dx == 3
    assert dy == 5
    assert confidence > 0.9


def test_find_dominant_phase_zero_energy() -> None:
    """Zero energy should give confidence 0."""
    energy_maps = np.zeros((8, 8, 16, 16), dtype=np.float32)
    _dx, _dy, confidence = _find_dominant_phase(energy_maps)
    assert confidence == 0.0


def test_analyze_jpeg_grid_basic() -> None:
    """Grid analysis should run on a simple array."""
    arr = np.random.randn(64, 64).astype(np.float32) * 50 + 128
    result = analyze_jpeg_grid(arr)
    assert result.origin.dx in range(8)
    assert result.origin.dy in range(8)
    assert 0.0 <= result.origin.confidence <= 1.0
    assert result.context in ("low_confidence", "moderate", "high_confidence")


def test_analyze_jpeg_grid_uniform() -> None:
    """Uniform image should have low confidence."""
    arr = np.full((64, 64, 3), 128, dtype=np.uint8)
    result = analyze_jpeg_grid(arr)
    assert result.origin.confidence < 0.5  # Low confidence for uniform image


def test_analyze_jpeg_grid_invalid() -> None:
    """Invalid input should be rejected."""
    with pytest.raises(InputRejectedError):
        analyze_jpeg_grid(np.array([]))
    with pytest.raises(InputRejectedError):
        analyze_jpeg_grid("not an array")  # type: ignore[arg-type]


def test_grid_result_to_dict() -> None:
    """Result should convert to plain dict."""
    arr = np.random.randn(64, 64, 3).astype(np.float32) * 50 + 128
    result = analyze_jpeg_grid(arr)
    d = grid_result_to_dict(result)
    assert "origin" in d
    assert "context" in d
    assert "anomaly" in d
    assert "dx" in d["origin"]
    assert "dy" in d["origin"]
    assert "confidence" in d["origin"]


def test_grid_map_structure() -> None:
    """Grid map anomaly should have correct structure."""
    arr = np.random.randn(128, 128, 3).astype(np.float32) * 50 + 128
    result = analyze_jpeg_grid(arr)
    anomaly = result.anomaly
    assert hasattr(anomaly, "values")
    assert hasattr(anomaly, "block")
    assert hasattr(anomaly, "stride")
    assert hasattr(anomaly, "scale")
    assert hasattr(anomaly, "valid_mask")
    assert hasattr(anomaly, "notes")


def test_window_stride_configuration() -> None:
    """Window and stride parameters should affect output."""
    arr = np.random.randn(128, 128, 3).astype(np.float32) * 50 + 128
    r1 = analyze_jpeg_grid(arr, window=32, stride=16)
    r2 = analyze_jpeg_grid(arr, window=64, stride=32)
    assert r1.anomaly.values.shape != r2.anomaly.values.shape


def test_grid_with_noise() -> None:
    """Grid analysis on pure noise should have low confidence."""
    arr = np.random.randn(128, 128).astype(np.float32) * 255
    result = analyze_jpeg_grid(arr)
    # Pure noise should have low grid confidence
    assert result.origin.confidence < 0.8

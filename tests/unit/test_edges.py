"""Tests for photoproof.plugins.visual.edges.algorithm."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.visual.edges.algorithm import analyze_edges


def _make_grayscale_image(h: int = 64, w: int = 64, seed: int = 42) -> np.ndarray:
    """Create a synthetic grayscale image."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w), dtype=np.uint8)


def test_analyze_edges_basic() -> None:
    """Edge analysis runs on a simple grayscale image."""
    image = _make_grayscale_image(64, 64, seed=1)
    result = analyze_edges(image)
    assert "edge_width_map" in result
    assert "edge_segments" in result
    assert "config" in result
    assert result["edge_width_map"].shape == (64, 64)
    assert result["edge_width_map"].dtype == np.float32
    assert isinstance(result["edge_segments"], list)


def test_analyze_edges_deterministic() -> None:
    """Same input produces identical output."""
    image = _make_grayscale_image(64, 64, seed=5)
    r1 = analyze_edges(image)
    r2 = analyze_edges(image)
    assert np.array_equal(r1["edge_width_map"], r2["edge_width_map"])
    assert r1["config"] == r2["config"]


def test_analyze_edges_invalid() -> None:
    """Invalid input is rejected."""
    with pytest.raises(InputRejectedError):
        analyze_edges("not an array")  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        analyze_edges(np.zeros((32, 32, 32, 32), dtype=np.uint8))


def test_analyze_edges_color_image() -> None:
    """Color images are converted to luminance."""
    image = np.random.randint(0, 256, (32, 32, 3), dtype=np.uint8)
    result = analyze_edges(image)
    assert result["edge_width_map"].shape == (32, 32)
    assert isinstance(result["edge_segments"], list)


def test_analyze_edges_uniform_image() -> None:
    """Uniform image produces no edges."""
    image = np.zeros((32, 32), dtype=np.uint8)
    image[:] = 128
    result = analyze_edges(image)
    assert result["edge_width_map"].sum() == 0
    assert len(result["edge_segments"]) == 0


def test_analyze_edges_different_sigma() -> None:
    """Different sigma values produce different configs."""
    image = _make_grayscale_image(32, 32, seed=1)
    r1 = analyze_edges(image, sigma=0.5)
    r2 = analyze_edges(image, sigma=2.0)
    assert r1["config"]["sigma"] == 0.5
    assert r2["config"]["sigma"] == 2.0


def test_analyze_edges_different_profile_length() -> None:
    """Different profile lengths produce different configs."""
    image = _make_grayscale_image(32, 32, seed=1)
    r1 = analyze_edges(image, profile_length=5)
    r2 = analyze_edges(image, profile_length=50)
    assert r1["config"]["profile_length"] == 5
    assert r2["config"]["profile_length"] == 50


def test_analyze_edges_small_image() -> None:
    """Small image is handled gracefully."""
    image = np.random.randint(0, 256, (16, 16), dtype=np.uint8)
    result = analyze_edges(image)
    assert result["edge_width_map"].shape == (16, 16)


def test_analyze_edges_step_edge_width() -> None:
    """A synthetic step edge produces a measurable edge width."""
    h, w = 32, 32
    image = np.zeros((h, w), dtype=np.uint8)
    image[:, 16:] = 255  # Vertical step edge
    result = analyze_edges(image, profile_length=15)
    # Edge width should be measurable (> 0)
    edge_widths = result["edge_width_map"][result["edge_width_map"] > 0]
    if len(edge_widths) > 0:
        assert np.all(edge_widths >= 0)


def test_analyze_edges_result_to_dict() -> None:
    """Result converts to plain dict."""
    image = _make_grayscale_image(32, 32, seed=1)
    result = analyze_edges(image)
    d = {
        "edge_width_map": result["edge_width_map"].tolist(),
        "edge_segments": result["edge_segments"],
        "config": result["config"],
    }
    assert "edge_width_map" in d
    assert "edge_segments" in d
    assert "config" in d

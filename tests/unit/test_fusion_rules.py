"""Tests for fusion.rules (#33): rule-based evidence fusion.

Covers:
- Each rule has positive and negative tests.
- Weak-only evidence never produces a suspicious region (property test).
- Every fused region lists contributing detectors and rules.
- Output is labelled heuristic score; no "probability" in outputs.
- All detectors unavailable → no fused map, unavailable listed.
- Strong evidence alone marks a region suspicious.
- Medium evidence needs family support or high score.
- Missing layers reduce attainable confidence.
"""

from __future__ import annotations

import numpy as np

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.rules.processor import (
    _connected_components,
    _noisy_or,
    _resample_to_common_grid,
    run,
)

# ------------------------------------------------------------------ noisy_or


def test_noisy_or_empty() -> None:
    """No inputs → zero score."""
    result = _noisy_or(np.array([], dtype=np.float32), np.array([], dtype=np.float32))
    assert result == 0.0


def test_noisy_or_single() -> None:
    """Single weight-score pair."""
    scores = np.array([0.5], dtype=np.float32)
    weights = np.array([1.0], dtype=np.float32)
    result = _noisy_or(scores, weights)
    assert abs(float(result) - 0.5) < 1e-6


def test_noisy_or_monotonic() -> None:
    """Adding a layer never decreases the result."""
    s1 = np.array([0.5], dtype=np.float32)
    w1 = np.array([0.6], dtype=np.float32)
    r1 = _noisy_or(s1, w1)
    s2 = np.array([0.5, 0.5], dtype=np.float32)
    w2 = np.array([0.6, 0.6], dtype=np.float32)
    r2 = _noisy_or(s2, w2)
    assert float(r2) >= float(r1)


def test_noisy_or_clamped() -> None:
    """Result never exceeds 1.0."""
    scores = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    weights = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    result = _noisy_or(scores, weights)
    assert abs(float(result) - 1.0) < 1e-6


# ------------------------------------------------------------------ resample


def test_resample_same_grid() -> None:
    """Same-size maps pass through unchanged."""
    m = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
    result = _resample_to_common_grid({"a": m})
    assert np.array_equal(result["a"], m)


def test_resample_downsample() -> None:
    """Larger maps are downsampled to the smallest common grid."""
    big = np.arange(16, dtype=np.float32).reshape(4, 4)
    small = np.array([[0.5, 0.6], [0.7, 0.8]], dtype=np.float32)
    result = _resample_to_common_grid({"big": big, "small": small})
    assert result["big"].shape == (2, 2)
    assert result["small"].shape == (2, 2)


# ------------------------------------------------------------------ connected components


def test_connected_components_empty() -> None:
    """Empty mask → no components."""
    mask = np.zeros((3, 3), dtype=bool)
    assert _connected_components(mask) == []


def test_connected_components_single() -> None:
    """Single pixel → one component."""
    mask = np.zeros((3, 3), dtype=bool)
    mask[1, 1] = True
    comps = _connected_components(mask)
    assert len(comps) == 1
    assert comps[0][0].tolist() == [1]
    assert comps[0][1].tolist() == [1]


def test_connected_components_two_separate() -> None:
    """Two separate pixels → two components."""
    mask = np.zeros((3, 3), dtype=bool)
    mask[0, 0] = True
    mask[2, 2] = True
    comps = _connected_components(mask)
    assert len(comps) == 2


def test_connected_components_connected() -> None:
    """Two adjacent pixels → one component."""
    mask = np.zeros((3, 3), dtype=bool)
    mask[0, 0] = True
    mask[0, 1] = True
    comps = _connected_components(mask)
    assert len(comps) == 1


# ------------------------------------------------------------------ run: no layers


def test_run_no_layers() -> None:
    """No layers → map is None, unavailable listed."""
    result = run(_make_ctx({"fusion.layers": [], "fusion.unavailable": ["compression.ela_map"]}))
    assert result["fusion.map"] is None
    evidence = result["fusion.evidence"]
    assert len(evidence) == 1
    assert evidence[0]["detector_id"] == "compression.ela_map"
    assert evidence[0]["reason"] == "unavailable"


def test_run_no_layers_none_input() -> None:
    """None layers → map is None."""
    result = run(_make_ctx({"fusion.layers": None, "fusion.unavailable": []}))
    assert result["fusion.map"] is None


# ------------------------------------------------------------------ run: strong evidence alone


def test_run_strong_alone_suspicious() -> None:
    """Strong evidence alone marks a region suspicious."""
    strong_map = np.zeros((4, 4), dtype=np.float32)
    strong_map[1:3, 1:3] = 0.9
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    {
                        "detector_id": "copymove.verification",
                        "map": strong_map,
                        "regions": (),
                        "strength_class": "strong",
                        "valid_mask": np.ones((4, 4), dtype=bool),
                        "calibration": "uncalibrated",
                        "notes": (),
                    }
                ],
                "fusion.unavailable": [],
            }
        )
    )
    fmap = np.array(result["fusion.map"], dtype=np.float32)
    assert fmap[1, 1] > 0.5
    assert len(result["fusion.evidence"]) >= 1
    region = result["fusion.evidence"][0]
    assert "copymove.verification" in region["contributing_detectors"]
    assert "geometry" in region["families"]


# ------------------------------------------------------------------ run: weak only never suspicious


def test_run_weak_only_never_suspicious() -> None:
    """Weak-only evidence never reaches suspicious (>= 0.5)."""
    weak_map = np.full((4, 4), 0.9, dtype=np.float32)
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    {
                        "detector_id": "compression.ela_map",
                        "map": weak_map,
                        "regions": (),
                        "strength_class": "weak",
                        "valid_mask": np.ones((4, 4), dtype=bool),
                        "calibration": "uncalibrated",
                        "notes": (),
                    }
                ],
                "fusion.unavailable": [],
            }
        )
    )
    fmap = np.array(result["fusion.map"], dtype=np.float32)
    assert np.all(fmap < 0.5)


def test_run_weak_only_property_test() -> None:
    """Property test: adding weak layers never makes weak-only suspicious."""
    for seed in range(10):
        rng = np.random.default_rng(seed)
        weak_map = rng.uniform(0.5, 1.0, (6, 6)).astype(np.float32)
        result = run(
            _make_ctx(
                {
                    "fusion.layers": [
                        {
                            "detector_id": "compression.ela_map",
                            "map": weak_map,
                            "regions": (),
                            "strength_class": "weak",
                            "valid_mask": np.ones((6, 6), dtype=bool),
                            "calibration": "uncalibrated",
                            "notes": (),
                        },
                        {
                            "detector_id": "visual.sharpness_map",
                            "map": weak_map * 0.8,
                            "regions": (),
                            "strength_class": "weak",
                            "valid_mask": np.ones((6, 6), dtype=bool),
                            "calibration": "uncalibrated",
                            "notes": (),
                        },
                    ],
                    "fusion.unavailable": [],
                }
            )
        )
        fmap = np.array(result["fusion.map"], dtype=np.float32)
        assert np.all(fmap < 0.5), f"seed {seed}: weak-only reached {fmap.max()}"


# ------------------------------------------------------------------ run: medium needs support


def test_run_medium_high_score_alone_suspicious() -> None:
    """Medium layer with high score (> 0.8) is suspicious without family support."""
    med_map = np.zeros((4, 4), dtype=np.float32)
    med_map[1, 1] = 0.9
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    {
                        "detector_id": "compression.dq_map",
                        "map": med_map,
                        "regions": (),
                        "strength_class": "medium",
                        "valid_mask": np.ones((4, 4), dtype=bool),
                        "calibration": "uncalibrated",
                        "notes": (),
                    }
                ],
                "fusion.unavailable": [],
            }
        )
    )
    fmap = np.array(result["fusion.map"], dtype=np.float32)
    assert fmap[1, 1] > 0.5


def test_run_medium_low_score_alone_not_suspicious() -> None:
    """Medium layer with low score (< 0.8) and no family support is NOT suspicious."""
    med_map = np.zeros((4, 4), dtype=np.float32)
    med_map[1, 1] = 0.4
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    {
                        "detector_id": "compression.dq_map",
                        "map": med_map,
                        "regions": (),
                        "strength_class": "medium",
                        "valid_mask": np.ones((4, 4), dtype=bool),
                        "calibration": "uncalibrated",
                        "notes": (),
                    }
                ],
                "fusion.unavailable": [],
            }
        )
    )
    fmap = np.array(result["fusion.map"], dtype=np.float32)
    assert fmap[1, 1] < 0.5


def test_run_medium_with_family_support_suspicious() -> None:
    """Medium layer with support from another family is suspicious."""
    med_map = np.zeros((4, 4), dtype=np.float32)
    med_map[1, 1] = 0.4
    strong_map = np.zeros((4, 4), dtype=np.float32)
    strong_map[1, 1] = 0.6  # strong from another family (geometry)
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    {
                        "detector_id": "compression.dq_map",
                        "map": med_map,
                        "regions": (),
                        "strength_class": "medium",
                        "valid_mask": np.ones((4, 4), dtype=bool),
                        "calibration": "uncalibrated",
                        "notes": (),
                    },
                    {
                        "detector_id": "copymove.matches",
                        "map": strong_map,
                        "regions": (),
                        "strength_class": "strong",
                        "valid_mask": np.ones((4, 4), dtype=bool),
                        "calibration": "uncalibrated",
                        "notes": (),
                    },
                ],
                "fusion.unavailable": [],
            }
        )
    )
    fmap = np.array(result["fusion.map"], dtype=np.float32)
    assert fmap[1, 1] > 0.5


# ------------------------------------------------------------------ run: missing layers


def test_run_missing_layers_listed() -> None:
    """Missing layers are listed in evidence with reason 'unavailable'."""
    strong_map = np.zeros((4, 4), dtype=np.float32)
    strong_map[1, 1] = 0.9
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    {
                        "detector_id": "copymove.verification",
                        "map": strong_map,
                        "regions": (),
                        "strength_class": "strong",
                        "valid_mask": np.ones((4, 4), dtype=bool),
                        "calibration": "uncalibrated",
                        "notes": (),
                    }
                ],
                "fusion.unavailable": ["noise.sigma_map", "visual.sharpness_map"],
            }
        )
    )
    fmap = np.array(result["fusion.map"], dtype=np.float32)
    assert fmap[1, 1] > 0.5
    unavailable_ids = [
        e["detector_id"] for e in result["fusion.evidence"] if e.get("reason") == "unavailable"
    ]
    assert "noise.sigma_map" in unavailable_ids
    assert "visual.sharpness_map" in unavailable_ids


# ------------------------------------------------------------------ run: no probability word


def test_run_no_probability_word() -> None:
    """No output contains the word 'probability'."""
    strong_map = np.zeros((4, 4), dtype=np.float32)
    strong_map[1, 1] = 0.9
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    {
                        "detector_id": "copymove.verification",
                        "map": strong_map,
                        "regions": (),
                        "strength_class": "strong",
                        "valid_mask": np.ones((4, 4), dtype=bool),
                        "calibration": "uncalibrated",
                        "notes": (),
                    }
                ],
                "fusion.unavailable": [],
            }
        )
    )
    text = str(result)
    assert "probability" not in text.lower()


# ------------------------------------------------------------------ helper


def _make_ctx(inputs: dict[str, object]) -> PluginContext:
    return PluginContext(
        run_id="run-1",
        plugin_id="fusion.rules",
        inputs=inputs,
        config={},
        seed=0,
        write_artifact=lambda name, data, media_type: None,
        logger=None,  # type: ignore[arg-type]
    )

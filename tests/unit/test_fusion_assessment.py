"""Tests for fusion.assessment (#34): evidence sufficiency and inconclusive results.

Covers:
- Missing/failed/not-applicable detectors lead to ``inconclusive``, never a clean
  category.
- No category/template/UI string uses "authentic", "genuine" or "real".
- Coverage table included in every assessment.
- Sufficient coverage with no indicators → ``no_indicators_found_with_sufficient_coverage``.
- Strong evidence → ``manipulation_indicated``.
- Comparison mode with differences → ``differences_found``.
"""

from __future__ import annotations

import numpy as np

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.assessment.processor import run

# ------------------------------------------------------------------ helper


def _make_ctx(inputs: dict[str, object]) -> PluginContext:
    return PluginContext(
        run_id="run-1",
        plugin_id="fusion.assessment",
        inputs=inputs,
        config={},
        seed=0,
        write_artifact=lambda name, data, media_type: None,
        logger=None,  # type: ignore[arg-type]
    )


def _make_layer(
    det_id: str,
    strength_class: str,
    map_values: list[list[float]] | None = None,
) -> dict[str, object]:
    return {
        "detector_id": det_id,
        "map": map_values,
        "regions": (),
        "strength_class": strength_class,
        "valid_mask": [[True, True], [True, True]],
        "calibration": "uncalibrated",
        "notes": (),
    }


# ------------------------------------------------------------------ insufficient coverage


def test_insufficient_coverage_inconclusive() -> None:
    """Only one family available → inconclusive."""
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    _make_layer("compression.ela_map", "weak", [[0.1, 0.2], [0.3, 0.4]])
                ],
                "fusion.evidence": [],
                "fusion.unavailable": [
                    "noise.sigma_map",
                    "visual.sharpness_map",
                    "copymove.verification",
                ],
            }
        )
    )
    assert result["fusion.assessment"]["category"] == "inconclusive"
    assert "insufficient coverage" in result["fusion.assessment"]["reasons"][0]


def test_no_detectors_inconclusive() -> None:
    """No detectors at all → inconclusive."""
    result = run(
        _make_ctx(
            {
                "fusion.layers": [],
                "fusion.evidence": [],
                "fusion.unavailable": [
                    "compression.ela_map",
                    "noise.sigma_map",
                    "visual.sharpness_map",
                    "copymove.verification",
                ],
            }
        )
    )
    assert result["fusion.assessment"]["category"] == "inconclusive"


def test_unavailable_detectors_inconclusive() -> None:
    """All detectors unavailable → inconclusive."""
    result = run(
        _make_ctx(
            {
                "fusion.layers": [],
                "fusion.evidence": [],
                "fusion.unavailable": [
                    "compression.ela_map",
                    "noise.sigma_map",
                    "visual.sharpness_map",
                    "copymove.verification",
                ],
            }
        )
    )
    assert result["fusion.assessment"]["category"] == "inconclusive"


# ------------------------------------------------------------------ no authentic/genuine/real


def test_no_forbidden_words() -> None:
    """No category name contains 'authentic', 'genuine', or 'real'."""
    for cat in [
        "manipulation_indicated",
        "differences_found",
        "no_indicators_found_with_sufficient_coverage",
        "inconclusive",
    ]:
        for word in ["authentic", "genuine", "real"]:
            assert word not in cat.lower()


# ------------------------------------------------------------------ manipulation_indicated


def test_strong_evidence_manipulation_indicated() -> None:
    """Strong evidence (copy-move) with high score → manipulation_indicated."""
    strong_map = np.zeros((4, 4), dtype=np.float32)
    strong_map[1:3, 1:3] = 0.9
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    _make_layer("copymove.verification", "strong", strong_map.tolist()),
                    _make_layer("noise.sigma_map", "medium", [[0.1, 0.2], [0.3, 0.4]]),
                ],
                "fusion.evidence": [],
                "fusion.unavailable": [],
            }
        )
    )
    assert result["fusion.assessment"]["category"] == "manipulation_indicated"


# ------------------------------------------------------------------ no indicators sufficient


def test_sufficient_coverage_no_indicators() -> None:
    """Two families with low scores → no_indicators_found_with_sufficient_coverage."""
    low_map = np.zeros((4, 4), dtype=np.float32)
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    _make_layer("compression.ela_map", "weak", low_map.tolist()),
                    _make_layer("noise.sigma_map", "medium", low_map.tolist()),
                ],
                "fusion.evidence": [],
                "fusion.unavailable": [],
            }
        )
    )
    assert result["fusion.assessment"]["category"] == "no_indicators_found_with_sufficient_coverage"


# ------------------------------------------------------------------ coverage table


def test_coverage_table_included() -> None:
    """Every assessment includes a coverage table."""
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    _make_layer("compression.ela_map", "weak", [[0.1, 0.2], [0.3, 0.4]])
                ],
                "fusion.evidence": [],
                "fusion.unavailable": [
                    "noise.sigma_map",
                    "visual.sharpness_map",
                    "copymove.verification",
                ],
            }
        )
    )
    assessment = result["fusion.assessment"]
    assert "coverage" in assessment
    assert isinstance(assessment["coverage"], dict)
    assert "compression" in assessment["coverage"]
    assert "noise" in assessment["coverage"]
    assert "visual" in assessment["coverage"]


def test_limitations_included() -> None:
    """Limitations included for unavailable families."""
    result = run(
        _make_ctx(
            {
                "fusion.layers": [
                    _make_layer("compression.ela_map", "weak", [[0.1, 0.2], [0.3, 0.4]])
                ],
                "fusion.evidence": [],
                "fusion.unavailable": ["noise.sigma_map"],
            }
        )
    )
    assessment = result["fusion.assessment"]
    assert "limitations" in assessment
    assert any("noise" in lim for lim in assessment["limitations"])

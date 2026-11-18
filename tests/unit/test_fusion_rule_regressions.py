"""Acceptance regressions for configurable, family-aware fusion."""

import logging

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.rules.processor import run
from photoproof.plugins.fusion.rules.schema import Config, StrengthWeights


def _run(layers, config=None):
    return run(
        PluginContext(
            run_id="regression",
            plugin_id="fusion.rules",
            inputs={"fusion.layers": layers},
            config=config or Config(),
            seed=0,
            write_artifact=lambda *args: None,
            logger=logging.getLogger(__name__),
        )
    )


def _layer(name, strength, score):
    return {"detector_id": name, "strength_class": strength, "map": [[score]]}


def test_correlated_medium_detectors_count_once():
    one = [_layer("compression.dq_map", "medium", 0.9)]
    two = [*one, _layer("compression.grid_map", "medium", 0.9)]
    assert np.allclose(_run(one)["fusion.map"], _run(two)["fusion.map"])


def test_configured_weight_changes_actual_score():
    layers = [_layer("copymove.mask", "strong", 0.9)]
    muted = Config(strength_weights=StrengthWeights(strong=0))
    assert _run(layers, muted)["fusion.map"] == [[0.0]]


def test_rejected_medium_cannot_enable_weak_boost():
    layers = [
        _layer("compression.dq_map", "medium", 0.1),
        _layer("compression.ela_map", "weak", 1.0),
    ]
    assert _run(layers)["fusion.map"] == [[0.0]]


def test_unavailable_has_no_zero_score():
    result = _run([{"detector_id": "noise.sigma_map", "map": None}])
    assert result["fusion.map"] is None
    assert result["fusion.evidence"][0]["score"] is None
    assert result["fusion.score_info"] == {
        "label": "heuristic_score",
        "calibration": "uncalibrated",
    }


def test_invalid_pixels_do_not_support_fusion():
    layer = _layer("copymove.mask", "strong", 0.9)
    layer["valid_mask"] = [[False]]
    assert _run([layer])["fusion.map"] == [[0.0]]


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_scores_fail_visibly(score):
    with pytest.raises(InputRejectedError, match="finite scores"):
        _run([_layer("copymove.mask", "strong", score)])


def test_only_effective_contributors_are_attributed():
    layers = [
        _layer("compression.dq_map", "medium", 0.9),
        _layer("compression.grid_map", "medium", 0.85),
        _layer("compression.ela_map", "weak", 1.0),
    ]
    evidence = _run(layers)["fusion.evidence"]
    assert evidence[0]["contributing_detectors"] == ["compression.dq_map"]


def test_global_scalar_context_does_not_collapse_spatial_localization():
    spatial = np.zeros((16, 16), dtype=np.float32)
    spatial[4:12, 4:12] = 0.9
    layers = [
        {"detector_id": "copymove.mask", "strength_class": "strong", "map": spatial.tolist()},
        {
            "detector_id": "compression.ela_summary",
            "strength_class": "weak",
            "map": [[0.8]],
            "notes": ["scalar_normalized"],
        },
    ]
    fused = np.asarray(_run(layers)["fusion.map"])
    assert fused.shape == spatial.shape
    assert fused[0, 0] == 0 and fused[8, 8] >= 0.9


def test_weak_boost_is_proportional_to_accepted_support():
    layers = [_layer("copymove.mask", "strong", 0.01), _layer("compression.ela_map", "weak", 1.0)]
    assert 0.01 < _run(layers)["fusion.map"][0][0] < 0.02

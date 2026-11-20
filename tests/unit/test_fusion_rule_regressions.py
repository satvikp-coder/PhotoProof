"""Family policy regressions; binary protocol and actual detectors have separate coverage."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.fusion.rules.schema import Config, StrengthWeights
from tests.fixtures.native_fusion import fuse_controls


def _layer(name, strength, score):
    return {"detector_id": name, "strength_class": strength, "map": [[score]]}


def test_correlated_medium_detectors_count_once():
    # Mathematical same-family control; raw JPEG tables are never normalized in production.
    one = [_layer("compression.dq_map", "medium", 0.9)]
    two = [*one, _layer("compression.jpeg_tables", "medium", 0.9)]
    np.testing.assert_allclose(fuse_controls(one).fused.scores, fuse_controls(two).fused.scores)


def test_configured_weight_changes_actual_score():
    layers = [_layer("copymove.mask", "strong", 0.9)]
    muted = Config(strength_weights=StrengthWeights(strong=0))
    assert fuse_controls(layers, muted).fused is None


def test_rejected_medium_cannot_enable_weak_boost():
    result = fuse_controls(
        [
            _layer("compression.dq_map", "medium", 0.1),
            _layer("compression.ela_anomaly", "weak", 1.0),
        ]
    )
    assert not result.fused.scores.any()


def test_unavailable_has_no_zero_score():
    result = fuse_controls([{"detector_id": "noise.consistency_map", "map": None}])
    assert result.fused is None


def test_invalid_pixels_do_not_support_fusion():
    layer = _layer("copymove.mask", "strong", 0.9)
    layer["valid_mask"] = [[False]]
    with pytest.raises(InputRejectedError):
        fuse_controls([layer])


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_scores_fail_visibly(score):
    with pytest.raises(InputRejectedError, match="invalid"):
        fuse_controls([_layer("copymove.mask", "strong", score)])


def test_only_effective_contributors_are_attributed():
    result = fuse_controls(
        [
            _layer("compression.dq_map", "medium", 0.9),
            _layer("compression.grid_map", "weak", 0.85),
            _layer("compression.ela_anomaly", "weak", 1.0),
        ]
    )
    names = sorted(name for name, values in result.contributions.items() if values.any())
    assert names == ["compression.dq_map"]


def test_global_scalar_context_does_not_collapse_spatial_localization():
    spatial = np.zeros((16, 16), np.float32)
    spatial[4:12, 4:12] = 0.9
    result = fuse_controls(
        [
            {"detector_id": "copymove.mask", "strength_class": "strong", "map": spatial},
            {
                "detector_id": "compression.ela_summary",
                "strength_class": "weak",
                "map": [[0.8]],
                "notes": ["scalar_normalized"],
            },
        ]
    )
    assert result.fused.scores.shape == spatial.shape
    assert result.fused.scores[0, 0] == 0 and result.fused.scores[8, 8] >= 0.9
    assert not result.measured["compression.ela_summary"].any()


def test_weak_boost_is_proportional_to_accepted_support():
    result = fuse_controls(
        [_layer("copymove.mask", "strong", 0.01), _layer("compression.ela_anomaly", "weak", 1.0)]
    )
    assert 0.01 < result.fused.scores[0, 0] < 0.02

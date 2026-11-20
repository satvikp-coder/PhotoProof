"""Deterministic rule controls: real sampling support and independent-family gates."""

import numpy as np
import pytest

from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.evidence_io import SpatialEvidence
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.grids import SamplingGrid
from photoproof.plugins.fusion.rules.native import fuse_native
from photoproof.plugins.fusion.rules.schema import Config, StrengthWeights
from tests.unit.test_score_grid_projection import _geometry


def _layer(name, strength, score, *, shape=(2, 3), stride=(4, 4), block=None, valid=None):
    values = np.full(shape, score, np.float32)
    validity = np.ones(shape, bool) if valid is None else valid
    return SpatialEvidence(
        EvidenceLayer(name, values, (), strength, validity, "uncalibrated"),
        "a" * 64,
        SamplingGrid((12, 8), (0, 0), stride, block or stride),
    )


def test_low_positive_independent_cues_do_not_enable_each_other():
    result = fuse_native(
        [
            _layer("compression.dq_map", "medium", 0.1),
            _layer("noise.consistency_map", "medium", 0.1),
        ],
        geometry=_geometry(12, 8),
    )
    assert not result.fused.scores.any()
    assert result.fused.valid_mask.all()
    assert not any(a.any() for a in result.contributions.values())


def test_credible_medium_families_combine_once():
    result = fuse_native(
        [
            _layer("compression.dq_map", "medium", 0.6),
            _layer("noise.consistency_map", "medium", 0.6),
        ],
        geometry=_geometry(12, 8),
    )
    np.testing.assert_allclose(result.fused.scores, 1 - (1 - 0.6 * 0.6) ** 2, atol=1e-6)
    assert all(a.any() for a in result.contributions.values())


def test_zero_weight_cannot_corroborate_or_establish_coverage():
    result = fuse_native(
        [_layer("copymove.mask", "strong", 1), _layer("noise.consistency_map", "medium", 0.6)],
        geometry=_geometry(12, 8),
        config=Config(strength_weights=StrengthWeights(strong=0)),
    )
    assert not result.fused.scores.any()
    assert not result.contributions["noise.consistency_map"].any()


def test_weak_only_is_unavailable_fusion_not_a_clean_full_frame():
    result = fuse_native([_layer("compression.ela_anomaly", "weak", 1)], geometry=_geometry(12, 8))
    assert result.fused is None and result.reason == "no_nonweak_measured_coverage"


def test_same_family_weak_cue_cannot_boost_its_medium_contribution():
    result = fuse_native(
        [_layer("compression.dq_map", "medium", 0.9), _layer("compression.grid_map", "weak", 1)],
        geometry=_geometry(12, 8),
    )
    np.testing.assert_allclose(result.fused.scores, 0.9 * 0.6, atol=1e-6)
    assert not result.contributions["compression.grid_map"].any()


def test_strong_independent_family_boost_has_precise_attribution():
    result = fuse_native(
        [_layer("copymove.mask", "strong", 0.8), _layer("compression.ela_anomaly", "weak", 1)],
        geometry=_geometry(12, 8),
    )
    np.testing.assert_allclose(
        result.fused.scores, 1 - (1 - 0.8) * (1 - 0.3 * 0.2 * 0.8), atol=1e-6
    )
    assert result.contributions["copymove.mask"].any()
    assert result.contributions["compression.ela_anomaly"].any()


def test_finest_sampling_preserved_with_gaps_and_invalid_cells():
    fine_valid = np.ones((8, 12), bool)
    fine_valid[:, :6] = False
    result = fuse_native(
        [
            _layer("copymove.mask", "strong", 0.9, shape=(8, 12), stride=(1, 1), valid=fine_valid),
            _layer("noise.consistency_map", "medium", 0.9, block=(2, 2)),
        ],
        geometry=_geometry(12, 8),
    )
    assert result.fused.scores.shape == (8, 12)
    assert not result.fused.valid_mask[3, 3] and result.fused.valid_mask[0, 0]
    assert not result.fused.scores[~result.fused.valid_mask].any()


@pytest.mark.parametrize("orientation", range(1, 9))
def test_native_fusion_uses_exact_working_frame_for_every_orientation(orientation):
    result = fuse_native(
        [_layer("copymove.mask", "strong", 0.9, shape=(8, 12), stride=(1, 1))],
        geometry=_geometry(12, 8, orientation),
    )
    expected = (12, 8) if orientation >= 5 else (8, 12)
    assert result.fused.scores.shape == expected
    np.testing.assert_allclose(result.fused.scores, 0.9, atol=1e-6)


def test_duplicate_detectors_are_rejected_instead_of_double_counted():
    layer = _layer("noise.consistency_map", "medium", 0.9)
    with pytest.raises(InputRejectedError, match="repeats"):
        fuse_native([layer, layer], geometry=_geometry(12, 8))


def test_fusion_budget_fails_before_projection_or_coordinate_allocation(monkeypatch):
    from dataclasses import replace

    from photoproof.plugins.fusion.rules import native

    geometry = _geometry(12, 8)
    geometry["decoded_size"] = geometry["working_size"] = [5000, 5000]
    layer = _layer("copymove.mask", "strong", 0.9, shape=(1, 1), stride=(1, 1))
    layer = replace(layer, sampling_grid=SamplingGrid((5000, 5000), (0, 0), (1, 1), (1, 1)))

    def forbidden(*args, **kwargs):
        raise AssertionError("budget must be checked before projecting any arrays")

    monkeypatch.setattr(native, "project_scores", forbidden)
    with pytest.raises(InputRejectedError, match="budget"):
        fuse_native([layer], geometry=geometry)


def test_original_maps_and_validity_are_never_modified():
    layer = _layer("copymove.mask", "strong", 0.9, shape=(8, 12), stride=(1, 1))
    before = layer.layer.map.tobytes(), layer.layer.valid_mask.tobytes()
    fuse_native([layer], geometry=_geometry(12, 8))
    assert (layer.layer.map.tobytes(), layer.layer.valid_mask.tobytes()) == before

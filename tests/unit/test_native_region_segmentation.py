"""Cleanup keeps measured support, exact provenance, holes and explicit metadata limits."""

import numpy as np
import pytest

from photoproof.contracts.contributions import Contribution
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import FusedScores
from photoproof.plugins.fusion.regions.native import segment_native
from photoproof.plugins.fusion.regions.schema import Config


def _case(scores, valid=None, contribution=None):
    valid = np.ones(scores.shape, bool) if valid is None else valid
    fused = FusedScores(scores, valid, "a" * 64, scores.shape[::-1])
    entry = Contribution(
        "copymove.mask",
        "geometry",
        "strong",
        1.0,
        "spatial",
        (),
        scores.copy() if contribution is None else contribution,
        valid.copy(),
    )
    return fused, (entry,)


def test_unmeasured_holes_are_never_filled_or_given_scores():
    values = np.zeros((48, 48), np.float32)
    values[8:36, 8:36] = 0.9
    valid = np.ones(values.shape, bool)
    valid[16:24, 16:24] = False
    values[~valid] = 0
    fused, entries = _case(values, valid)
    mask, regions, summary = segment_native(fused, entries)
    assert not mask[~valid].any()
    assert len(regions) == 1 and regions[0]["holes"]
    assert regions[0]["area"] == int(mask.sum())
    assert regions[0]["contributing_detectors"] == ["copymove.mask"]
    assert summary["invalid_pixels_retained"] == 0
    assert summary["source_sha256"] == "a" * 64


def test_removed_high_seed_keeps_its_original_component_attribution():
    values = np.zeros((48, 48), np.float32)
    values[8:32, 8:32] = 0.4
    values[8, 8] = 0.9
    contribution = np.zeros_like(values)
    contribution[8, 8] = 0.9
    fused, entries = _case(values, contribution=contribution)
    mask, regions, _ = segment_native(fused, entries)
    assert mask.any() and not mask[8, 8]
    assert regions[0]["contributing_detectors"] == ["copymove.mask"]
    assert regions[0]["max_score"] == pytest.approx(0.4)


def test_region_metadata_cap_retains_complete_mask_and_reports_truncation():
    values = np.zeros((48, 48), np.float32)
    values[4:12, 4:12] = 0.9
    values[28:36, 28:36] = 0.9
    fused, entries = _case(values)
    mask, regions, summary = segment_native(fused, entries, config=Config(max_regions=1))
    assert mask[8, 8] and mask[32, 32]
    assert len(regions) == 1 and summary["region_count"] == 2
    assert summary["truncated"] and summary["omitted_region_count"] == 1


def test_thin_boundary_is_not_replaced_with_an_invented_box_polygon():
    values = np.zeros((8, 8), np.float32)
    values[3, 4] = 0.9
    fused, entries = _case(values)
    mask, regions, _ = segment_native(
        fused, entries, config=Config(kernel_size=1, min_area_fraction=0)
    )
    assert mask.sum() == 1
    assert regions[0]["polygon"] == [[4, 3]]
    assert regions[0]["boundary_kind"] == "degenerate_pixel_centers"


def test_positive_region_without_spatial_attribution_is_rejected():
    values = np.zeros((32, 32), np.float32)
    values[8:24, 8:24] = 0.9
    fused, _ = _case(values)
    with pytest.raises(InputRejectedError, match="no spatial contribution"):
        segment_native(fused, ())


def test_region_explanations_name_actual_accepted_gates_and_component_scope():
    values = np.zeros((32, 32), np.float32)
    values[8:24, 8:24] = 0.9
    fused, entries = _case(values)
    _, regions, _ = segment_native(fused, entries)
    item = regions[0]["contributions"][0]
    assert item["detector_id"] == "copymove.mask"
    assert item["family"] == "geometry"
    assert item["effective_weight"] == 1
    assert item["rules_fired"] == ["strong_maximum"]
    assert item["accepted_cell_count"] == 256
    assert item["max_accepted_score"] == pytest.approx(0.9)
    assert item["mean_accepted_score"] == pytest.approx(0.9)
    assert item["measurement_scope"] == "original_hysteresis_components"
    assert regions[0]["families"] == ["geometry"]
    assert regions[0]["rules_fired"] == ["strong_maximum"]


def test_removed_seed_statistics_are_not_relabelled_as_final_polygon_scores():
    values = np.zeros((48, 48), np.float32)
    values[8:32, 8:32] = 0.4
    values[8, 8] = 0.9
    contribution = np.zeros_like(values)
    contribution[8, 8] = 0.9
    fused, entries = _case(values, contribution=contribution)
    mask, regions, _ = segment_native(fused, entries)
    assert not mask[8, 8]
    assert regions[0]["max_score"] == pytest.approx(0.4)
    detail = regions[0]["contributions"][0]
    assert detail["accepted_cell_count"] == 1
    assert detail["max_accepted_score"] == pytest.approx(0.9)
    assert detail["measurement_scope"] == "original_hysteresis_components"


def test_zero_contributions_do_not_claim_rules_or_families():
    from dataclasses import replace

    values = np.zeros((32, 32), np.float32)
    values[8:24, 8:24] = 0.9
    fused, entries = _case(values)
    suppressed = replace(
        entries[0],
        detector_id="noise.consistency_map",
        family="noise",
        strength_class="medium",
        scores=np.zeros_like(values),
    )
    _, regions, _ = segment_native(fused, (*entries, suppressed))
    assert regions[0]["families"] == ["geometry"]
    assert [e["detector_id"] for e in regions[0]["contributions"]] == ["copymove.mask"]


@pytest.mark.parametrize("strong", [False, True])
def test_explanations_follow_native_medium_and_weak_gates(strong):
    from photoproof.contracts.contributions import Contribution
    from tests.fixtures.native_fusion import fuse_controls

    shape = (24, 24)
    specs = [
        {
            "detector_id": "compression.dq_map",
            "strength_class": "medium",
            "map": np.full(shape, 0.95, np.float32),
        },
        {
            "detector_id": "compression.ela_anomaly",
            "strength_class": "weak",
            "map": np.full(shape, 0.9, np.float32),
        },
        {
            "detector_id": "noise.consistency_map",
            "strength_class": "weak",
            "map": np.full(shape, 0.9, np.float32),
        },
    ]
    if strong:
        specs.append(
            {
                "detector_id": "copymove.mask",
                "strength_class": "strong",
                "map": np.full(shape, 0.9, np.float32),
            }
        )
    outcome = fuse_controls(specs)
    entries = tuple(
        Contribution(
            name,
            outcome.families[name],
            outcome.strengths[name],
            1.0,
            "spatial",
            (),
            scores,
            outcome.measured[name],
        )
        for name, scores in outcome.contributions.items()
    )
    _, regions, _ = segment_native(outcome.fused, entries, config=Config(high=0.5, low=0.3))
    details = {e["detector_id"]: e for e in regions[0]["contributions"]}
    assert (
        "medium_high_or_independent_family_support" in details["compression.dq_map"]["rules_fired"]
    )
    assert "independent_family_maximum" in details["compression.dq_map"]["rules_fired"]
    # Correlated ELA cannot double-count its winning compression-family medium cue.
    assert "compression.ela_anomaly" not in details
    assert "weak_boost_existing_nonweak_support" in details["noise.consistency_map"]["rules_fired"]
    assert ("strong_maximum" in regions[0]["rules_fired"]) is strong

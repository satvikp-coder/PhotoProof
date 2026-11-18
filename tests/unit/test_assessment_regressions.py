"""Coverage gates must respect configuration, validity and actual evidence."""

import logging

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.assessment import METADATA
from photoproof.plugins.fusion.assessment.processor import run
from photoproof.plugins.fusion.assessment.schema import Config


def _run(*, strength="medium", valid=True, minimum=2, unavailable=None):
    layers = [
        {"detector_id": name, "strength_class": strength, "map": [[0.0]], "valid_mask": [[valid]]}
        for name in ["compression.dq_map", "noise.consistency_map"]
    ]
    ctx = PluginContext(
        run_id="coverage",
        plugin_id="fusion.assessment",
        seed=0,
        config=Config(minimum_families_for_sufficient_coverage=minimum),
        inputs={
            "fusion.layers": layers,
            "fusion.map": [[0.0]],
            "fusion.unavailable": unavailable or [],
        },
        logger=logging.getLogger(__name__),
        write_artifact=lambda *args: None,
    )
    return run(ctx)["fusion.assessment"]


def test_weak_only_families_are_insufficient():
    assert _run(strength="weak")["category"] == "inconclusive"


def test_invalid_maps_do_not_count_as_coverage():
    assert _run(valid=False)["category"] == "inconclusive"


def test_configured_family_minimum_is_applied():
    assert _run(minimum=3)["category"] == "inconclusive"


def test_missing_detector_cannot_yield_clean_category():
    assert _run(unavailable=["copymove.mask"])["category"] == "inconclusive"


def test_metadata_declares_consumed_coverage_inputs():
    assert {"fusion.layers", "fusion.unavailable"} <= set(METADATA["optional_inputs"])

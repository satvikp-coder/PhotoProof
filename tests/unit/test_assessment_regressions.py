"""Coverage gates must respect configuration, validity and actual evidence."""

from photoproof.plugins.fusion.assessment import METADATA
from photoproof.plugins.fusion.assessment.processor import run
from photoproof.plugins.fusion.assessment.schema import Config


def _run(*, strength="medium", valid=True, minimum=2, unavailable=None):
    from photoproof.plugins.fusion.rules.native_processor import run_native
    from tests.unit.test_native_assessment import _assessment

    ctx = _assessment(low=True)
    for layer in ctx.inputs["fusion.layers"]:
        layer["strength_class"] = strength
    for fact in ctx.inputs["fusion.availability"]["outputs"].values():
        if fact["available"]:
            fact["strength_class"] = strength
            if not valid:
                fact.update(available=False, reason="no_usable_measurements")
    if not valid:
        ctx.inputs["fusion.layers"] = []  # All-invalid layers cannot be serialized as evidence.
    if unavailable:
        ctx.inputs["fusion.availability"]["required_unavailable"] = [
            {"output": name, "state": "SUCCESS", "reason": "no_usable_measurements"}
            for name in unavailable
        ]
    ctx.config = None
    ctx.inputs.update(run_native(ctx))
    ctx.config = Config(minimum_families_for_sufficient_coverage=minimum)
    return run(ctx)["fusion.assessment"]


def test_weak_only_families_are_insufficient():
    assert _run(strength="weak")["category"] == "inconclusive"


def test_invalid_maps_do_not_count_as_coverage():
    assert _run(valid=False)["category"] == "inconclusive"


def test_configured_family_minimum_is_applied():
    assert _run(minimum=3)["category"] == "inconclusive"


def test_missing_required_detector_cannot_yield_clean_category():
    assert _run(unavailable=["copymove.mask"])["category"] == "inconclusive"


def test_metadata_declares_consumed_coverage_inputs():
    assert {"fusion.evidence", "fusion.availability", "image.geometry"} <= set(
        METADATA["optional_inputs"]
    )

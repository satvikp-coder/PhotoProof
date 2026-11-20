"""Coverage truth tables use typed binary fusion; categories remain explicitly limited."""

import json
import re

import numpy as np
import pytest

from photoproof.contracts.arrays import encode_npy
from photoproof.plugins.fusion.assessment.native_processor import assess_native
from photoproof.plugins.fusion.normalize import DETECTOR_FAMILIES
from photoproof.plugins.fusion.normalize.native_processor import NATIVE_OUTPUTS
from photoproof.plugins.fusion.rules.native_processor import run_native
from tests.unit.test_binary_native_fusion_entrypoint import _context


def _assessment(*, low=False, candidate=False):
    ctx, _ = _context()
    if low:
        for ordinal, layer in enumerate(ctx.inputs["fusion.layers"]):
            layer["map"] = ctx.write_artifact(
                f"control-low-{ordinal}.npy",
                encode_npy(np.full((2, 3), 0.1, np.float32)),
                "application/x-npy",
            )
    if candidate:
        ctx.inputs["fusion.layers"][0]["notes"] = ["localized_search_scope"]
    result = run_native(ctx)
    ctx.inputs.update(result)
    available = {layer["detector_id"]: layer for layer in ctx.inputs["fusion.layers"]}
    outputs = {}
    for name in NATIVE_OUTPUTS:
        outputs[name] = {
            "family": DETECTOR_FAMILIES[name],
            "state": "SUCCESS" if name in available else "NOT_PLANNED",
            "available": name in available,
            "strength_class": available[name]["strength_class"] if name in available else None,
        }
    ctx.inputs["fusion.availability"] = {
        "format_version": 1,
        "source_sha256": "a" * 64,
        "calibration": "uncalibrated",
        "outputs": outputs,
        "required_status_known": True,
        "required_unavailable": [],
    }
    ctx.config = None
    return ctx


def test_independent_measured_families_can_indicate_provisional_manipulation():
    result = assess_native(_assessment())["fusion.assessment"]
    assert result["category"] == "manipulation_indicated"
    assert (
        result["coverage"]["compression"] == result["coverage"]["noise"] == "applicable_available"
    )
    assert result["calibration"] == "uncalibrated" and result["limitations"]


def test_measured_low_scores_with_sufficient_families_have_limited_negative_category():
    result = assess_native(_assessment(low=True))["fusion.assessment"]
    assert result["category"] == "no_indicators_found_with_sufficient_coverage"
    assert result["limitations"]  # Optional absent families remain visible.


def test_candidate_windows_do_not_support_full_frame_negative_conclusion():
    result = assess_native(_assessment(low=True, candidate=True))["fusion.assessment"]
    assert result["category"] == "inconclusive"
    assert any("candidate-window" in reason for reason in result["reasons"])


@pytest.mark.parametrize(
    "known,missing", [(False, []), (True, [{"output": "metadata.findings", "state": "FAILED"}])]
)
def test_required_failure_or_unknown_status_forces_inconclusive_even_with_positive_scores(
    known, missing
):
    ctx = _assessment()
    ctx.inputs["fusion.availability"]["required_status_known"] = known
    ctx.inputs["fusion.availability"]["required_unavailable"] = missing
    result = assess_native(ctx)["fusion.assessment"]
    assert result["category"] == "inconclusive"
    assert any("required" in reason for reason in result["reasons"])


def test_all_not_applicable_is_inconclusive_with_coverage_table():
    ctx = _assessment()
    ctx.inputs["fusion.map"] = ctx.inputs["fusion.evidence"] = None
    for fact in ctx.inputs["fusion.availability"]["outputs"].values():
        fact.update(state="NOT_APPLICABLE", available=False, strength_class=None)
    result = assess_native(ctx)["fusion.assessment"]
    assert result["category"] == "inconclusive" and result["coverage"]
    assert all(state in {"not_applicable", "not_planned"} for state in result["coverage"].values())


@pytest.mark.parametrize("low", [False, True])
def test_assessment_templates_have_no_forbidden_claim_words(low):
    text = json.dumps(assess_native(_assessment(low=low)))
    for word in ("authentic", "genuine", "real"):
        assert not re.search(r"\b" + word + r"\b", text, flags=re.IGNORECASE)

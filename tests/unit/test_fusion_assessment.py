"""Assessment policy controls use typed binary fusion, not unverified raw detector summaries.

Actual verified detector bindings and image pixels are covered by controller tests.
"""

import numpy as np

from photoproof.contracts.arrays import encode_npy
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.assessment.processor import run
from photoproof.plugins.fusion.rules.native_processor import run_native
from tests.unit.test_native_assessment import _assessment


def _empty():
    return PluginContext(
        run_id="coverage",
        plugin_id="fusion.assessment",
        inputs={},
        config={},
        seed=0,
        write_artifact=lambda *args: None,
        logger=None,
    )


def test_insufficient_coverage_inconclusive():
    ctx = _assessment(low=True)
    ctx.inputs["fusion.layers"] = ctx.inputs["fusion.layers"][:1]
    ctx.inputs["fusion.unavailable"] = ["noise.consistency_map", "copymove.mask"]
    ctx.inputs["fusion.availability"]["outputs"]["noise.consistency_map"].update(
        available=False, state="FAILED", strength_class=None
    )
    ctx.inputs.update(run_native(ctx))
    result = run(ctx)["fusion.assessment"]
    assert result["category"] == "inconclusive"
    assert any("insufficient" in reason for reason in result["reasons"])


def test_no_detectors_inconclusive():
    result = run(_empty())["fusion.assessment"]
    assert result["category"] == "inconclusive" and result["coverage"]


def test_unavailable_detectors_inconclusive():
    ctx = _assessment()
    ctx.inputs["fusion.map"] = ctx.inputs["fusion.evidence"] = None
    for fact in ctx.inputs["fusion.availability"]["outputs"].values():
        fact.update(available=False, state="FAILED", strength_class=None)
    assert run(ctx)["fusion.assessment"]["category"] == "inconclusive"


def test_no_forbidden_words():
    for category in [
        "manipulation_indicated",
        "differences_found",
        "no_indicators_found_with_sufficient_coverage",
        "inconclusive",
    ]:
        for word in ["authentic", "genuine", "real"]:
            assert word not in category.lower()


def test_strong_evidence_manipulation_indicated():
    ctx = _assessment()
    layer = ctx.inputs["fusion.layers"][0]
    layer.update(
        detector_id="copymove.mask",
        strength_class="strong",
        notes=[
            "verified_dense_pixel_similarity",
            "original_source_bound",
            "localized_search_scope",
            "mask_sha256:" + "a" * 64,
            "verification_bindings_sha256:" + "b" * 64,
        ],
    )
    layer["map"] = ctx.write_artifact(
        "strong-policy-control.npy",
        encode_npy(np.full((2, 3), 0.9, np.float32)),
        "application/x-npy",
    )
    ctx.inputs["fusion.unavailable"] = []
    outputs = ctx.inputs["fusion.availability"]["outputs"]
    outputs["compression.dq_map"].update(available=False, state="NOT_PLANNED", strength_class=None)
    outputs["copymove.mask"].update(available=True, state="SUCCESS", strength_class="strong")
    ctx.inputs.update(run_native(ctx))
    assert run(ctx)["fusion.assessment"]["category"] == "manipulation_indicated"


def test_sufficient_coverage_no_indicators():
    result = run(_assessment(low=True))["fusion.assessment"]
    assert result["category"] == "no_indicators_found_with_sufficient_coverage"
    assert result["calibration"] == "uncalibrated"


def test_coverage_table_included():
    coverage = run(_empty())["fusion.assessment"]["coverage"]
    assert {"compression", "noise", "visual", "geometry"} <= set(coverage)


def test_limitations_included():
    result = run(_empty())["fusion.assessment"]
    assert any("noise" in limitation for limitation in result["limitations"])

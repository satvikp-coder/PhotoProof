"""Processor wiring for fusion.assessment.

Produces the run's overall assessment category based on coverage (which detectors
were available and applicable). Missing evidence leads to ``inconclusive`` rather
than an implied clean result.

Output: ``fusion.assessment``: ``{category, reasons[], coverage{family: state},
limitations[]}``.

Categories (none contain "authentic", "genuine", or "real"):
- ``manipulation_indicated``: strong evidence of manipulation found
- ``differences_found`` (comparison mode only): differences between reference and target
- ``no_indicators_found_with_sufficient_coverage``: sufficient coverage but no
  manipulation indicators
- ``inconclusive``: insufficient coverage, missing detectors, or all detectors failed
"""

from __future__ import annotations

from typing import Any

import numpy as np

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.assessment import (
    CATEGORY_INCONCLUSIVE,
    CATEGORY_MANIPULATION_INDICATED,
    CATEGORY_NO_INDICATORS_SUFFICIENT_COVERAGE,
    DETECTOR_FAMILIES,
)
from photoproof.plugins.fusion.assessment.schema import Config


def _compute_coverage(
    layers: list[dict[str, Any]],
    unavailable: list[str],
) -> dict[str, str]:
    """Compute coverage per detector family.

    Returns a dict mapping family name to state:
    - ``applicable_available``: detector ran and produced output
    - ``applicable_unavailable``: detector exists but produced no output
    - ``not_applicable``: detector not applicable to this input format
    """
    coverage = dict.fromkeys(DETECTOR_FAMILIES.values(), "not_applicable")
    ranks = {
        "not_applicable": 0,
        "applicable_unavailable": 1,
        "low_confidence": 2,
        "applicable_available": 3,
    }
    for det_id, family in DETECTOR_FAMILIES.items():
        state = "applicable_unavailable" if det_id in unavailable else "not_applicable"
        for layer in layers:
            if not isinstance(layer, dict) or layer.get("detector_id") != det_id:
                continue
            try:
                values = np.asarray(layer.get("map"), dtype=np.float32)
                valid = np.asarray(layer.get("valid_mask"))
                usable = (
                    values.ndim == 2
                    and values.size > 0
                    and np.isfinite(values).all()
                    and np.all((values >= 0) & (values <= 1))
                    and valid.dtype == np.bool_
                    and valid.shape == values.shape
                    and valid.any()
                )
            except (TypeError, ValueError, OverflowError):
                usable = False
            if usable:
                state = (
                    "applicable_available"
                    if layer.get("strength_class") in {"medium", "strong"}
                    else "low_confidence"
                )
            else:
                state = "applicable_unavailable"
            if ranks[state] > ranks[coverage[family]]:
                coverage[family] = state
        if ranks[state] > ranks[coverage[family]]:
            coverage[family] = state
    return coverage


def _count_available_families(coverage: dict[str, str]) -> int:
    """Count distinct families with applicable_available state."""
    families = set()
    for fam, state in coverage.items():
        if state == "applicable_available":
            families.add(fam)
    return len(families)


def run(ctx: PluginContext) -> dict[str, object]:
    """Assess evidence sufficiency and produce an assessment category.

    Reads ``fusion.map`` and ``fusion.evidence`` (optional inputs from
    ``fusion.rules``). Produces ``fusion.assessment``.
    """
    layers = ctx.inputs.get("fusion.layers", [])
    unavailable = ctx.inputs.get("fusion.unavailable", [])

    if not isinstance(layers, list):
        layers = []
    if isinstance(unavailable, tuple):
        unavailable = list(unavailable)
    if not isinstance(unavailable, list):
        unavailable = ["invalid_availability_record"]

    coverage = _compute_coverage(layers, unavailable)
    available_families = _count_available_families(coverage)

    config = ctx.config if isinstance(ctx.config, Config) else Config.model_validate(ctx.config)
    reasons: list[str] = []
    if available_families < config.minimum_families_for_sufficient_coverage:
        reasons.append(
            f"insufficient coverage: {available_families} families available, "
            f"{config.minimum_families_for_sufficient_coverage} required"
        )
    if unavailable:
        reasons.append("one or more detector outputs are unavailable")
    try:
        fused = np.asarray(ctx.inputs.get("fusion.map"), dtype=np.float32)
        has_fused = (
            fused.ndim == 2
            and fused.size > 0
            and np.isfinite(fused).all()
            and np.all((fused >= 0) & (fused <= 1))
        )
    except (TypeError, ValueError, OverflowError):
        has_fused = False
    if not has_fused:
        reasons.append("fused evidence is unavailable or invalid")
    invalid_families = any(state == "applicable_unavailable" for state in coverage.values())
    if invalid_families:
        reasons.append("one or more detector families have no valid output")
    if reasons:
        category = CATEGORY_INCONCLUSIVE
    elif float(fused.max()) > config.manipulation_confidence_threshold:
        # Use accepted fusion, never raw keypoints or a free-text keyword search.
        category = CATEGORY_MANIPULATION_INDICATED
        reasons = ["fused heuristic indicators exceed the provisional threshold"]
    else:
        category = CATEGORY_NO_INDICATORS_SUFFICIENT_COVERAGE
        reasons = ["no indicators above threshold in the available non-weak coverage"]

    # Build limitations
    limitations: list[str] = ["coverage and thresholds are provisional and uncalibrated"]
    for fam, state in coverage.items():
        if state != "applicable_available":
            limitations.append(f"{fam}: {state}")

    assessment = {
        "category": category,
        "reasons": reasons,
        "coverage": coverage,
        "limitations": limitations,
    }

    return {"fusion.assessment": assessment}

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
    CATEGORY_DIFFERENCES_FOUND,
    CATEGORY_INCONCLUSIVE,
    CATEGORY_MANIPULATION_INDICATED,
    CATEGORY_NO_INDICATORS_SUFFICIENT_COVERAGE,
    DETECTOR_FAMILIES,
    SUFFICIENT_COVERAGE_MIN_FAMILIES,
)


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
    coverage: dict[str, str] = {}
    for det_id in DETECTOR_FAMILIES:
        fam = DETECTOR_FAMILIES[det_id]
        # Check if this detector produced a layer
        layer = next(
            (layer_entry for layer_entry in layers if layer_entry.get("detector_id") == det_id),
            None,
        )
        if layer is not None:
            layer_map = layer.get("map")
            if layer_map is not None and len(layer_map) > 0:
                # Only set applicable_available; don't downgrade from a previous
                # applicable_available for the same family.
                if coverage.get(fam) != "applicable_available":
                    coverage[fam] = "applicable_available"
            elif fam not in coverage:
                coverage[fam] = "applicable_unavailable"
        elif det_id in unavailable:
            if fam not in coverage:
                coverage[fam] = "applicable_unavailable"
        elif fam not in coverage:
            coverage[fam] = "not_applicable"
    return coverage


def _count_available_families(coverage: dict[str, str]) -> int:
    """Count distinct families with applicable_available state."""
    families = set()
    for fam, state in coverage.items():
        if state == "applicable_available":
            families.add(fam)
    return len(families)


def _has_manipulation_indicators(layers: list[dict[str, Any]]) -> bool:
    """Check if any strong evidence layer indicates manipulation."""
    for layer in layers:
        if layer.get("strength_class") == "strong":
            layer_map = layer.get("map")
            if layer_map is not None:
                arr = np.array(layer_map, dtype=np.float32)
                if arr.size > 0 and float(arr.max()) > 0.5:
                    return True
    return False


def run(ctx: PluginContext) -> dict[str, object]:
    """Assess evidence sufficiency and produce an assessment category.

    Reads ``fusion.map`` and ``fusion.evidence`` (optional inputs from
    ``fusion.rules``). Produces ``fusion.assessment``.
    """
    layers = ctx.inputs.get("fusion.layers", [])
    evidence = ctx.inputs.get("fusion.evidence", [])
    unavailable = ctx.inputs.get("fusion.unavailable", [])

    if not isinstance(layers, list):
        layers = []
    if not isinstance(evidence, list):
        evidence = []
    if not isinstance(unavailable, list):
        unavailable = []

    coverage = _compute_coverage(layers, unavailable)
    available_families = _count_available_families(coverage)

    # Determine category
    if available_families < SUFFICIENT_COVERAGE_MIN_FAMILIES:
        category = CATEGORY_INCONCLUSIVE
        reasons = [
            f"insufficient coverage: {available_families} families available, "
            f"{SUFFICIENT_COVERAGE_MIN_FAMILIES} required"
        ]
    elif _has_manipulation_indicators(layers):
        category = CATEGORY_MANIPULATION_INDICATED
        reasons = ["manipulation indicators detected"]
    else:
        # Check if this is comparison mode (fusion.evidence may have comparison info)
        has_comparison = any(
            "comparison" in str(e).lower() or "reference" in str(e).lower() for e in evidence
        )
        if has_comparison:
            category = CATEGORY_DIFFERENCES_FOUND
            reasons = ["differences found between reference and target"]
        else:
            category = CATEGORY_NO_INDICATORS_SUFFICIENT_COVERAGE
            reasons = ["no manipulation indicators found with sufficient coverage"]

    # Build limitations
    limitations: list[str] = []
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

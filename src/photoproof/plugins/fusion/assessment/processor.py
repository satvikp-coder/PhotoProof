"""Production entry point for source-bound binary evidence.

The registered run uses the coordinated native implementation. Retained private
numerical helpers are historical mathematical controls, not alternate wire readers.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.assessment import (
    DETECTOR_FAMILIES,
)
from photoproof.plugins.fusion.assessment.native_processor import assess_native


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
    """Run the source-bound binary production pipeline."""
    return assess_native(ctx)

"""Assessment from measured independent families and actual required-producer facts."""

from typing import cast

import numpy as np

from photoproof.contracts.contributions import read_contributions
from photoproof.contracts.enums import ExecutionState
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import read_fused_scores
from photoproof.contracts.grids import validate_pixel_transform
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.assessment import (
    CATEGORY_INCONCLUSIVE,
    CATEGORY_MANIPULATION_INDICATED,
    CATEGORY_NO_INDICATORS_SUFFICIENT_COVERAGE,
)
from photoproof.plugins.fusion.assessment.schema import Config
from photoproof.plugins.fusion.normalize import DETECTOR_FAMILIES, NATIVE_OUTPUTS


def assess_native(ctx: PluginContext) -> dict[str, object]:
    """Report bounded heuristic categories; partial candidate searches cannot clear an image."""
    config = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )
    families = sorted(set(DETECTOR_FAMILIES.values()))
    coverage = dict.fromkeys(families, "not_planned")
    rank = {
        "not_planned": 0,
        "not_applicable": 1,
        "applicable_unavailable": 2,
        "weight_disabled": 3,
        "low_confidence": 4,
        "applicable_available": 5,
    }
    details: dict[str, object] = {}
    reasons = []
    limitations = ["coverage and thresholds are provisional and uncalibrated"]
    geometry_value = ctx.inputs.get("image.geometry")
    if geometry_value is not None and not isinstance(geometry_value, dict):
        raise InputRejectedError("assessment requires exact host image geometry")
    geometry = cast(ImageGeometry | None, geometry_value)
    if geometry is not None:
        validate_pixel_transform(geometry)
    else:
        reasons.append("host image geometry is unavailable")
    availability = ctx.inputs.get("fusion.availability")
    outputs = {}
    if availability is None:
        reasons.append("normalization availability is unavailable")
    elif (
        not isinstance(availability, dict)
        or type(availability.get("format_version")) is not int
        or availability["format_version"] != 1
        or availability.get("calibration") != "uncalibrated"
        or not isinstance(availability.get("outputs"), dict)
        or set(availability["outputs"]) != set(NATIVE_OUTPUTS)
        or (geometry is not None and availability.get("source_sha256") != geometry["source_sha256"])
        or type(availability.get("required_status_known")) is not bool
        or not isinstance(availability.get("required_unavailable"), list)
    ):
        raise InputRejectedError("assessment availability source, state or format is invalid")
    else:
        outputs = availability["outputs"]
        if not availability["required_status_known"]:
            reasons.append("required detector status is unavailable")
        if availability["required_unavailable"]:
            reasons.append("one or more required detector methods have no usable result")
    fused = read_fused_scores(ctx, geometry=geometry) if geometry is not None else None
    entries = (
        read_contributions(ctx, geometry=geometry, fused=fused)
        if fused is not None and geometry is not None
        else ()
    )
    by_name = {entry.detector_id: entry for entry in entries}
    if fused is None:
        reasons.append("fused measured evidence is unavailable")
    available_families = set()
    negative_families = set()
    available_names = set()
    for name in NATIVE_OUTPUTS:
        fact = outputs.get(name)
        if fact is None:
            continue
        if (
            not isinstance(fact, dict)
            or fact.get("family") != DETECTOR_FAMILIES[name]
            or type(fact.get("available")) is not bool
            or not isinstance(fact.get("state"), str)
            or fact["state"] not in {s.value for s in ExecutionState} | {"NOT_PLANNED"}
            or (fact["available"] and fact["state"] != "SUCCESS")
        ):
            raise InputRejectedError(
                "assessment detector state contradicts its measured availability"
            )
        family = DETECTOR_FAMILIES[name]
        state = (
            "not_planned"
            if fact["state"] == "NOT_PLANNED"
            else (
                "not_applicable" if fact["state"] == "NOT_APPLICABLE" else "applicable_unavailable"
            )
        )
        if fact["available"]:
            available_names.add(name)
            entry = by_name.get(name)
            if entry is not None:
                if entry.family != family or entry.strength_class != fact.get("strength_class"):
                    raise InputRejectedError(
                        "assessment contribution family or strength is inconsistent"
                    )
                if entry.effective_weight == 0:
                    state = "weight_disabled"
                elif entry.strength_class == "weak" or entry.scope == "global_context":
                    state = "low_confidence"
                elif entry.measured.any():
                    state = "applicable_available"
                    available_families.add(family)
                    if "localized_search_scope" not in entry.notes:
                        negative_families.add(family)
            elif fact.get("strength_class") == "weak":
                state = "low_confidence"
        if rank[state] > rank[coverage[family]]:
            coverage[family] = state
        details[name] = {**fact, "coverage_state": state}
        if state != "applicable_available":
            limitations.append(f"{name}: {state}")
    if fused is not None and set(by_name) != available_names:
        raise InputRejectedError(
            "assessment contribution listing differs from normalized availability"
        )
    minimum = config.minimum_families_for_sufficient_coverage
    if len(available_families) < minimum:
        reasons.append(f"insufficient measured families: {len(available_families)} of {minimum}")
    category = CATEGORY_INCONCLUSIVE
    if not reasons and fused is not None:
        if float(np.max(fused.scores[fused.valid_mask])) > config.manipulation_confidence_threshold:
            category = CATEGORY_MANIPULATION_INDICATED
            reasons = ["accepted fused heuristic indicators exceed the provisional threshold"]
        elif len(negative_families) < minimum:
            reasons = ["candidate-window coverage cannot support a full-frame no-indicator result"]
        else:
            category = CATEGORY_NO_INDICATORS_SUFFICIENT_COVERAGE
            reasons = ["no indicators above threshold in sufficient independent measured coverage"]
    limitations.extend(
        f"{family}: {state}"
        for family, state in coverage.items()
        if state != "applicable_available"
    )
    return {
        "fusion.assessment": {
            "category": category,
            "reasons": reasons,
            "coverage": coverage,
            "coverage_details": details,
            "limitations": limitations,
            "calibration": "uncalibrated",
        }
    }

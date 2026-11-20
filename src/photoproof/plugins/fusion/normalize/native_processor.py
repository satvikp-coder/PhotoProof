"""Coordinated binary normalization entry point; activated only with migrated consumers."""

import re
from typing import Literal, cast

from photoproof.contracts.enums import ExecutionState
from photoproof.contracts.evidence_io import SpatialEvidence, write_evidence_layer
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.grids import validate_pixel_transform
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize import (
    CONTEXT_OUTPUTS,
    DETECTOR_FAMILIES,
    DETECTOR_NORMALIZATION,
    NATIVE_OUTPUTS,
)
from photoproof.plugins.fusion.normalize.copymove import read_copy_move
from photoproof.plugins.fusion.normalize.native import read_native_anomaly
from photoproof.plugins.fusion.normalize.schema import Config


def _status(ctx: PluginContext, name: str) -> dict[str, object]:
    snapshot = ctx.inputs.get("pipeline.upstream_status")
    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("format_version")) is not int
        or snapshot["format_version"] != 1
        or not isinstance(snapshot.get("outputs"), dict)
    ):
        raise InputRejectedError("native normalization requires the host upstream-state snapshot")
    record = snapshot["outputs"].get(name)
    if record is None:
        return {"plugin_id": None, "state": "NOT_PLANNED", "failure_category": None}
    if (
        not isinstance(record, dict)
        or set(record) != {"plugin_id", "state", "failure_category"}
        or not isinstance(record["plugin_id"], str)
        or not isinstance(record["state"], str)
        or record["state"] not in {s.value for s in ExecutionState}
        or (
            record["failure_category"] is not None
            and not isinstance(record["failure_category"], str)
        )
    ):
        raise InputRejectedError(
            f"native normalization has invalid host execution facts for {name}"
        )
    return dict(record)


def _measurement(
    ctx: PluginContext,
    name: str,
    geometry: ImageGeometry,
    config: Config,
) -> SpatialEvidence | None:
    value = ctx.inputs.get(name)
    if name not in {"copymove.mask", "noise.consistency_map"} and (
        not isinstance(value, dict) or not value or set(value) == {"artifact"}
    ):
        return None  # Unsupported/unread values lack the native scale and sampling contract.
    defaults = DETECTOR_NORMALIZATION[name]
    override = config.mappings.get(name)
    strength = str(defaults["strength_class"])
    if override is not None and override.strength_class is not None:
        strength = override.strength_class
    expected = "identity" if name == "copymove.mask" else "robust_z"
    if override is not None and override.method is not None and override.method != expected:
        raise InputRejectedError(f"{name} native scale requires mapping method {expected}")
    if name == "copymove.mask":
        return read_copy_move(
            ctx,
            geometry=geometry,
            strength_class=cast(Literal["weak", "medium", "strong"], strength),
            minimum_geometric_support=(
                override.minimum_geometric_support
                if override is not None and override.minimum_geometric_support is not None
                else 0.8
            ),
        )
    if strength not in {"weak", "medium"}:
        raise InputRejectedError(f"{name} cannot be promoted into strong evidence")
    z0 = float(cast(float, defaults["z0"]))
    k = float(cast(float, defaults["k"]))
    if override is not None:
        z0 = z0 if override.z0 is None else override.z0
        k = k if override.k is None else override.k
    return read_native_anomaly(
        ctx,
        name,
        geometry=geometry,
        strength_class=cast(Literal["weak", "medium"], strength),
        z0=z0,
        k=k,
    )


def normalize_native(ctx: PluginContext) -> dict[str, object]:
    """Write measured binary layers and explicit applicability, never diagnostic pseudo-maps."""
    config = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )
    geometry_value = ctx.inputs.get("image.geometry")
    geometry: ImageGeometry | None = None
    if geometry_value is not None:
        if (
            not isinstance(geometry_value, dict)
            or not isinstance(geometry_value.get("source_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", geometry_value["source_sha256"])
        ):
            raise InputRejectedError("native normalization requires source-bound image geometry")
        geometry = cast(ImageGeometry, geometry_value)
        validate_pixel_transform(geometry)
    records: list[dict[str, object]] = []
    availability: dict[str, object] = {}
    unavailable = []
    for name in NATIVE_OUTPUTS:
        state = _status(ctx, name)
        reason = str(state["state"]).lower()
        measurement = None
        if geometry is None:
            reason = "geometry_unavailable"
        elif state["state"] == "SUCCESS":
            if name in ctx.inputs and ctx.inputs[name] is not None:
                measurement = _measurement(ctx, name, geometry, config)
                reason = "available" if measurement is not None else "no_usable_measurements"
            else:
                reason = "missing_output"
        elif name in ctx.inputs:
            raise InputRejectedError(f"{name} value contradicts its host execution state")
        details: dict[str, object] = {
            **state,
            "family": DETECTOR_FAMILIES[name],
            "reason": reason,
            "available": measurement is not None,
        }
        if measurement is None:
            unavailable.append(name)
        else:
            record = write_evidence_layer(ctx, measurement, ordinal=len(records))
            records.append(record)
            details.update(
                strength_class=measurement.layer.strength_class,
                measured_cells=int(measurement.layer.valid_mask.sum()),
                grid_cells=int(measurement.layer.valid_mask.size),
                sampling_grid=record["sampling_grid"],
                scope="candidate_windows"
                if "localized_search_scope" in measurement.layer.notes
                else "native_grid",
            )
        availability[name] = details
    snapshot = ctx.inputs.get("pipeline.upstream_status")
    required = snapshot.get("required_prior_outputs") if isinstance(snapshot, dict) else None
    required_known = isinstance(required, list)
    required_unavailable = []
    if required is not None and not required_known:
        raise InputRejectedError("required upstream-output listing must be a list")
    if isinstance(required, list):
        if len(required) > 512 or any(not isinstance(name, str) for name in required):
            raise InputRejectedError("required upstream-output listing is invalid")
        for name in required:
            state = _status(ctx, name)
            native = availability.get(name)
            if state["state"] != "SUCCESS" or (
                isinstance(native, dict) and not native["available"]
            ):
                reason = (
                    native["reason"] if isinstance(native, dict) else str(state["state"]).lower()
                )
                required_unavailable.append({"output": name, **state, "reason": reason})
    return {
        "fusion.layers": records,
        "fusion.unavailable": tuple(sorted(unavailable)),
        "fusion.availability": {
            "format_version": 1,
            "source_sha256": geometry["source_sha256"] if geometry is not None else None,
            "calibration": "uncalibrated",
            "outputs": availability,
            "required_status_known": required_known,
            "required_unavailable": required_unavailable,
            "context_only": sorted(set(ctx.inputs) & set(CONTEXT_OUTPUTS)),
        },
    }

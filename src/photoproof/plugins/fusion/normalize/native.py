"""Normalize actual regional anomaly records without guessing their pixel footprint.

This adapter is a prerequisite for the coordinated version-3 producer/consumer
migration. The current production processor does not yet enable it.
"""

import math
from dataclasses import replace
from typing import Literal

import numpy as np

from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.evidence_io import SpatialEvidence
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.grids import SamplingGrid
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext


def read_native_anomaly(
    ctx: PluginContext,
    name: str,
    *,
    geometry: ImageGeometry,
    strength_class: Literal["weak", "medium"],
    z0: float,
    k: float,
) -> SpatialEvidence | None:
    """Read existing producer records/artifacts, preserving their explicit scale.

    Missing companion sampling metadata is unavailable, never inferred from shape.
    This does not enable production v3 output or migrate downstream consumers.
    """
    if name not in {
        "compression.ela_anomaly",
        "compression.dq_map",
        "compression.grid_map",
        "noise.consistency_map",
    }:
        raise InputRejectedError(f"{name}: no native regional anomaly adapter is defined")
    if name not in ctx.inputs:
        return None
    record = ctx.inputs[name]
    low_confidence = False
    if name == "compression.dq_map":
        if not isinstance(record, dict) or "anomaly" not in record:
            raise InputRejectedError("compression.dq_map lacks its explicit anomaly record")
        record = record["anomaly"]
    elif name == "noise.consistency_map":
        summary = ctx.inputs.get("noise.variance_summary")
        if summary is None:
            return None
        if not isinstance(summary, dict) or not isinstance(summary.get("consistency_map"), dict):
            raise InputRejectedError(
                "noise consistency lacks its native sampling/validity metadata"
            )
        confidence = summary.get("confidence")
        if confidence is None:
            return None
        if not isinstance(confidence, str) or confidence not in {"high", "medium", "low"}:
            raise InputRejectedError("noise consistency has invalid reliability metadata")
        low_confidence = confidence == "low"
        record = dict(summary["consistency_map"])
        # Read only the declared score artifact through the verified broker. The
        # duplicated legacy JSON score values are not the authoritative payload.
        record["values"] = decode_npy(ctx.artifact(name), expected_ndim=2)
    size = geometry["decoded_size"]
    if len(size) != 2:
        raise InputRejectedError("native anomaly geometry needs decoded [width,height]")
    result = normalize_anomaly(
        record,
        detector_id=name,
        source_sha256=geometry["source_sha256"],
        image_size=(size[0], size[1]),
        strength_class=strength_class,
        z0=z0,
        k=k,
        tail="two_sided" if name == "noise.consistency_map" else "upper",
    )
    if result is not None and low_confidence:
        # Configured strength is a ceiling, never a license to promote unreliable
        # measurements into sufficient non-weak coverage.
        return replace(
            result,
            layer=replace(
                result.layer, strength_class="weak", notes=(*result.layer.notes, "low_confidence")
            ),
        )
    return result


def _pair(value: object, *, name: str) -> tuple[int, int]:
    if isinstance(value, int) and not isinstance(value, bool):
        value = [value, value]
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in value)
    ):
        raise InputRejectedError(f"anomaly {name} must contain positive integer [height,width]")
    return value[1], value[0]  # Producer metadata uses y/x; SamplingGrid uses x/y.


def normalize_anomaly(
    record: object,
    *,
    detector_id: str,
    source_sha256: str,
    image_size: tuple[int, int],
    strength_class: Literal["weak", "medium"],
    z0: float,
    k: float,
    tail: Literal["upper", "two_sided"],
) -> SpatialEvidence | None:
    """Map measured robust-z values monotonically to uncalibrated unit scores.

    Return None for an empty/all-invalid measurement. Invalid cells have zero fill
    with validity=false, distinguishable from a measured zero anomaly. Native
    robust-z values are not robust-standardized a second time. Two-sided scoring
    must be selected explicitly for detectors where either sign is meaningful.
    """
    if (
        not math.isfinite(z0)
        or z0 < 0
        or not math.isfinite(k)
        or k <= 0
        or tail not in {"upper", "two_sided"}
    ):
        raise InputRejectedError(
            "anomaly normalization needs finite z0>=0, k>0 and an explicit tail"
        )
    if not isinstance(record, dict) or not {
        "values",
        "valid_mask",
        "block",
        "stride",
        "scale",
        "notes",
    }.issubset(record):
        raise InputRejectedError(
            "anomaly record lacks explicit values, validity or sampling metadata"
        )
    if record["scale"] != "robust_z":
        raise InputRejectedError(
            "anomaly scale must be robust_z; raw statistics need their own mapping"
        )
    try:
        values = np.asarray(record["values"])
        valid = np.asarray(record["valid_mask"])
    except (ValueError, TypeError, OverflowError) as error:
        raise InputRejectedError(
            "anomaly values and validity must be rectangular arrays"
        ) from error
    if values.size == valid.size == 0:
        return None
    if (
        values.ndim != 2
        or values.dtype.kind not in "iuf"
        or values.size > 16_777_216
        or valid.dtype != np.bool_
        or valid.shape != values.shape
    ):
        raise InputRejectedError(
            "anomaly needs bounded 2D numeric values and matching boolean validity"
        )
    block, stride = _pair(record["block"], name="block"), _pair(record["stride"], name="stride")
    grid = SamplingGrid.from_record(
        {
            "image_size": list(image_size),
            "origin_xy": [0, 0],
            "block_xy": list(block),
            "stride_xy": list(stride),
        }
    )
    if any(
        (count - 1) * step >= size
        for count, step, size in zip(
            (values.shape[1], values.shape[0]), grid.stride_xy, grid.image_size, strict=True
        )
    ):
        raise InputRejectedError("anomaly sampling cells extend outside their source image")
    notes = record["notes"]
    if (
        not isinstance(notes, (list, tuple))
        or len(notes) > 12
        or any(not isinstance(n, str) or len(n) > 256 for n in notes)
    ):
        raise InputRejectedError("anomaly notes must be bounded text")
    if not valid.any():
        return None
    measured = values[valid].astype(np.float64)
    if not np.isfinite(measured).all():
        raise InputRejectedError("anomaly contains nonfinite measured values")
    magnitude = np.abs(measured) if tail == "two_sided" else np.maximum(measured, 0)
    baseline = 1 / (1 + math.exp(min(z0 / k, 50)))
    # Finite extremes may overflow the intermediate division; clipping defines
    # saturation, so suppress only this expected arithmetic warning locally.
    with np.errstate(over="ignore"):
        sigmoid = 1 / (1 + np.exp(np.clip(-(magnitude - z0) / k, -50, 50)))
    scores = np.zeros(values.shape, dtype=np.float32)
    scores[valid] = np.clip((sigmoid - baseline) / (1 - baseline), 0, 1)
    return SpatialEvidence(
        EvidenceLayer(
            detector_id=detector_id,
            map=scores,
            regions=(),
            strength_class=strength_class,
            valid_mask=valid.copy(),
            calibration="uncalibrated",
            notes=(*notes, f"native_robust_z_{tail}", "zero_anchored_logistic"),
        ),
        source_sha256,
        grid,
    )

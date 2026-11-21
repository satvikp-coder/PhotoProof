"""Versioned binary evidence layers, with explicit source and sampling provenance."""

import re
from dataclasses import dataclass
from typing import Literal, cast

import numpy as np

from photoproof.contracts.arrays import decode_npy, encode_npy
from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.grids import SamplingGrid
from photoproof.contracts.plugins import PluginContext

_FIELDS = {
    "format_version",
    "source_sha256",
    "coordinate_space",
    "sampling_grid",
    "detector_id",
    "map",
    "valid_mask",
    "regions",
    "strength_class",
    "calibration",
    "notes",
}


@dataclass(frozen=True)
class SpatialEvidence:
    """Decoded native-grid or explicitly global evidence; never an assumed pixel grid."""

    layer: EvidenceLayer
    source_sha256: str
    sampling_grid: SamplingGrid | None


def _validate(evidence: SpatialEvidence) -> None:
    layer, grid = evidence.layer, evidence.sampling_grid
    if not isinstance(evidence.source_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", evidence.source_sha256
    ):
        raise InputRejectedError("evidence source must be an explicit SHA-256")
    if (
        not isinstance(layer.detector_id, str)
        or len(layer.detector_id) > 100
        or not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", layer.detector_id)
    ):
        raise InputRejectedError("evidence detector_id must name its declared output contract")
    if not isinstance(layer.strength_class, str) or layer.strength_class not in {
        "weak",
        "medium",
        "strong",
    }:
        raise InputRejectedError("evidence strength class is invalid")
    if layer.calibration != "uncalibrated":
        raise InputRejectedError(
            "calibration needs verified data; current wire format is uncalibrated"
        )
    values, valid = layer.map, layer.valid_mask
    if (
        not isinstance(values, np.ndarray)
        or values.ndim != 2
        or not values.size
        or values.dtype.kind not in "iuf"
        or not np.isfinite(values).all()
        or np.any((values < 0) | (values > 1))
        or not isinstance(valid, np.ndarray)
        or valid.dtype != np.bool_
        or valid.shape != values.shape
        or not valid.any()
    ):
        raise InputRejectedError("evidence needs finite unit scores and valid measured pixels")
    if grid is None:
        if values.shape != (1, 1) or layer.regions:
            raise InputRejectedError("global evidence must have one score and no spatial regions")
        if layer.strength_class != "weak":
            raise InputRejectedError("global context cannot become localized non-weak evidence")
    else:
        if not isinstance(grid, SamplingGrid):
            raise InputRejectedError("evidence sampling grid is invalid")
        grid = SamplingGrid.from_record(grid.to_record())
        for axis, count in enumerate((values.shape[1], values.shape[0])):
            if grid.origin_xy[axis] + (count - 1) * grid.stride_xy[axis] >= grid.image_size[axis]:
                raise InputRejectedError("evidence cells extend outside their source image")
    if not isinstance(layer.regions, (list, tuple)) or len(layer.regions) > 64:
        raise InputRejectedError("evidence exceeds its bounded region metadata")
    for box in layer.regions:
        if (
            len(box) != 4
            or any(isinstance(n, bool) or not isinstance(n, int) for n in box)
            or min(box) < 0
            or box[2] <= box[0]
            or box[3] <= box[1]
            or grid is None
            or box[2] > grid.image_size[1]
            or box[3] > grid.image_size[0]
        ):
            raise InputRejectedError(
                "evidence region bounds disagree with its decoded coordinate space"
            )
    if (
        not isinstance(layer.notes, (list, tuple))
        or len(layer.notes) > 16
        or any(not isinstance(note, str) or len(note) > 4096 for note in layer.notes)
        or sum(len(note.encode("utf-8")) for note in layer.notes) > 4096
    ):
        raise InputRejectedError("evidence notes exceed their bounded UTF-8 metadata")


def write_evidence_layer(
    ctx: PluginContext, evidence: SpatialEvidence, *, ordinal: int
) -> dict[str, object]:
    """Persist canonical NPY score/mask bytes and compact version-2 provenance."""
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or not 0 <= ordinal < 256:
        raise InputRejectedError("evidence ordinal must be an integer from 0 to 255")
    _validate(evidence)
    layer, grid = evidence.layer, evidence.sampling_grid
    # _validate established an actual array; unavailable detectors cannot reach serialization.
    data = encode_npy(np.asarray(cast(np.ndarray, layer.map), dtype=np.float32))
    mask = encode_npy(layer.valid_mask)
    coordinate_space: Literal["global", "decoded_pixels"] = (
        "global" if grid is None else "decoded_pixels"
    )
    return {
        "format_version": 2,
        "source_sha256": evidence.source_sha256,
        "coordinate_space": coordinate_space,
        "sampling_grid": None if grid is None else grid.to_record(),
        "detector_id": layer.detector_id,
        "map": ctx.write_artifact(f"evidence-{ordinal:03d}-scores.npy", data, "application/x-npy"),
        "valid_mask": ctx.write_artifact(
            f"evidence-{ordinal:03d}-valid.npy", mask, "application/x-npy"
        ),
        "regions": [list(box) for box in layer.regions],
        "strength_class": layer.strength_class,
        "calibration": layer.calibration,
        "notes": list(layer.notes),
    }


def read_evidence_layer(
    ctx: PluginContext, name: str, *, ordinal: int, source_sha256: str
) -> SpatialEvidence:
    """Read one declared collection member through exact broker selectors, never paths."""
    records = ctx.inputs.get(name)
    if (
        isinstance(ordinal, bool)
        or not isinstance(ordinal, int)
        or not isinstance(records, list)
        or not 0 <= ordinal < min(256, len(records))
    ):
        raise InputRejectedError("evidence ordinal does not name a declared collection member")
    record = records[ordinal]
    if (
        not isinstance(record, dict)
        or set(record) != _FIELDS
        or not isinstance(record["format_version"], int)
        or record["format_version"] != 2
    ):
        raise InputRejectedError("unsupported or incomplete evidence-layer wire format")
    space = record["coordinate_space"]
    if not isinstance(space, str) or space not in {"global", "decoded_pixels"}:
        raise InputRejectedError("evidence coordinate space is unknown; do not assume alignment")
    grid = SamplingGrid.from_record(record["sampling_grid"]) if space == "decoded_pixels" else None
    if space == "global" and record["sampling_grid"] is not None:
        raise InputRejectedError("global evidence must not carry spatial sampling metadata")
    if (
        not isinstance(record["source_sha256"], str)
        or not isinstance(record["detector_id"], str)
        or not isinstance(record["regions"], list)
        or not all(isinstance(box, list) for box in record["regions"])
        or not isinstance(record["notes"], list)
        or not isinstance(record["strength_class"], str)
        or not isinstance(record["calibration"], str)
    ):
        raise InputRejectedError("evidence metadata has invalid field types")
    if record["source_sha256"] != source_sha256:
        raise InputRejectedError("evidence source does not match the declared image geometry")
    values = decode_npy(ctx.artifact(name, selector=(ordinal, "map")), expected_ndim=2)
    valid = decode_npy(ctx.artifact(name, selector=(ordinal, "valid_mask")), expected_ndim=2)
    if values.dtype != np.float32 or valid.dtype != np.bool_:
        raise InputRejectedError("version-2 evidence requires float32 scores and boolean validity")
    layer = EvidenceLayer(
        detector_id=record["detector_id"],
        map=values,
        regions=tuple(tuple(box) for box in record["regions"]),
        strength_class=record["strength_class"],
        valid_mask=valid,
        calibration=record["calibration"],
        notes=tuple(record["notes"]),
    )
    result = SpatialEvidence(layer, record["source_sha256"], grid)
    _validate(result)
    return result

"""Bounded fused-score transport in explicit normalized-working coordinates."""

import re
from dataclasses import dataclass

import numpy as np

from photoproof.contracts.arrays import decode_npy, encode_npy
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext

MAX_FUSED_CELLS = 4_194_304
_FIELDS = {
    "format_version",
    "source_sha256",
    "working_size",
    "grid_shape",
    "coordinate_space",
    "calibration",
    "scores",
    "valid_mask",
}


@dataclass(frozen=True)
class FusedScores:
    """Uniform working-image cells with distinct measured and unmeasured zeros."""

    scores: np.ndarray
    valid_mask: np.ndarray
    source_sha256: str
    working_size: tuple[int, int]


def _size(value: object, *, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n < 2**31 for n in value)
    ):
        raise InputRejectedError(f"fused {label} needs two positive 32-bit dimensions")
    return value[0], value[1]


def _validate(value: FusedScores) -> None:
    width, height = _size(value.working_size, label="working_size")
    if not isinstance(value.source_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", value.source_sha256
    ):
        raise InputRejectedError("fused source must be an explicit SHA-256")
    scores, valid = value.scores, value.valid_mask
    if (
        not isinstance(scores, np.ndarray)
        or scores.ndim != 2
        or not scores.size
        or scores.size > MAX_FUSED_CELLS
        or scores.shape[0] > height
        or scores.shape[1] > width
        or scores.dtype != np.float32
        or not np.isfinite(scores).all()
        or np.any((scores < 0) | (scores > 1))
        or not isinstance(valid, np.ndarray)
        or valid.dtype != np.bool_
        or valid.shape != scores.shape
        or not valid.any()
    ):
        raise InputRejectedError(
            "fused scores need bounded float32 unit values and measured boolean validity"
        )
    if np.any(scores[~valid] != 0):
        raise InputRejectedError("unmeasured fused cells must have zero fill and invalid status")


def write_fused_scores(ctx: PluginContext, value: FusedScores) -> dict[str, object]:
    """Write compact provenance and canonical NPY arrays, without original access."""
    _validate(value)
    scores = encode_npy(value.scores, max_elements=MAX_FUSED_CELLS)
    valid = encode_npy(value.valid_mask, max_elements=MAX_FUSED_CELLS)
    return {
        "format_version": 1,
        "source_sha256": value.source_sha256,
        "working_size": list(value.working_size),
        "grid_shape": list(value.scores.shape),
        "coordinate_space": "uniform_working_image_cells",
        "calibration": "uncalibrated",
        "scores": ctx.write_artifact("fused-scores.npy", scores, "application/x-npy"),
        "valid_mask": ctx.write_artifact("fused-valid.npy", valid, "application/x-npy"),
    }


def read_fused_scores(
    ctx: PluginContext, *, geometry: ImageGeometry, name: str = "fusion.map"
) -> FusedScores | None:
    """Read only source-matching declared artifacts; None means unavailable."""
    record = ctx.inputs.get(name)
    if record is None:
        return None
    if (
        not isinstance(record, dict)
        or set(record) != _FIELDS
        or isinstance(record["format_version"], bool)
        or not isinstance(record["format_version"], int)
        or record["format_version"] != 1
        or record["coordinate_space"] != "uniform_working_image_cells"
        or record["calibration"] != "uncalibrated"
    ):
        raise InputRejectedError("unsupported fused-score format, coordinates or calibration")
    size = _size(record["working_size"], label="working_size")
    height, width = _size(record["grid_shape"], label="grid_shape")
    if height * width > MAX_FUSED_CELLS or height > size[1] or width > size[0]:
        raise InputRejectedError("fused grid exceeds its allocation or source-resolution ceiling")
    if record["source_sha256"] != geometry["source_sha256"] or size != _size(
        geometry["working_size"], label="expected working_size"
    ):
        raise InputRejectedError("fused source or working dimensions disagree with image geometry")
    scores = decode_npy(
        ctx.artifact(name, selector=("scores",)), max_elements=MAX_FUSED_CELLS, expected_ndim=2
    )
    valid = decode_npy(
        ctx.artifact(name, selector=("valid_mask",)), max_elements=MAX_FUSED_CELLS, expected_ndim=2
    )
    if scores.shape != (height, width):
        raise InputRejectedError("fused NPY shape disagrees with its explicit grid metadata")
    result = FusedScores(scores, valid, record["source_sha256"], size)
    _validate(result)
    return result

"""Source-bound registration transport for measured reference-to-target comparisons.

Hashes establish artifact consistency, not the provenance or trustworthiness of either
user-supplied image. This module performs no filesystem access or image-byte decoding.
"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

import numpy as np

from photoproof.contracts.arrays import decode_npy, encode_npy
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext

MAX_REGISTRATION_PIXELS = 4194304
_NAMES = ("aligned_reference", "overlap_mask", "inlier_points")
_FIELDS = {
    "format_version",
    "status",
    "direction",
    "coordinate_convention",
    "target_sha256",
    "reference_sha256",
    "target_size",
    "reference_size",
    "requested_model",
    "model",
    "matrix",
    "reference_channels",
    "quality",
    "parameters",
    "flags",
    "artifacts",
}
_MODELS = {"similarity", "affine", "homography"}


@dataclass(frozen=True)
class RegisteredComparison:
    matrix: np.ndarray
    aligned_reference: np.ndarray
    overlap_mask: np.ndarray
    inlier_points: np.ndarray
    model: str
    quality: Mapping[str, object]


def _frame(geometry: ImageGeometry) -> tuple[str, list[int]]:
    if (
        not isinstance(geometry, dict)
        or type(geometry.get("schema_version")) is not int
        or geometry["schema_version"] != 1
        or geometry.get("coordinate_convention") != "zero_based_pixel_centres_xy"
        or not isinstance(geometry.get("source_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", geometry["source_sha256"])
    ):
        raise InputRejectedError("registration requires exact source-bound working geometry")
    size = geometry.get("working_size")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(type(n) is not int or n < 1 for n in size)
        or size[0] * size[1] > MAX_REGISTRATION_PIXELS
    ):
        raise InputRejectedError(
            "registration working dimensions exceed their four-million-pixel budget"
        )
    return geometry["source_sha256"], size


def _scalars(value: object, *, maximum: int) -> dict[str, object]:
    if not isinstance(value, dict) or len(value) > maximum:
        raise InputRejectedError(
            "registration quality and parameters must be bounded scalar mappings"
        )
    for name, item in value.items():
        if (
            not isinstance(name, str)
            or len(name) > 100
            or type(item) not in {str, int, float, bool, type(None)}
            or (isinstance(item, str) and len(item) > 512)
            or (type(item) is int and item.bit_length() > 256)
            or (type(item) is float and (not math.isfinite(item) or abs(item) > 1e18))
        ):
            raise InputRejectedError("registration metadata contains unbounded or nonfinite data")
    return value


def _metadata(
    record: object, *, target_geometry: ImageGeometry, reference_geometry: ImageGeometry
) -> dict[str, object]:
    target_sha, target_size = _frame(target_geometry)
    reference_sha, reference_size = _frame(reference_geometry)
    if (
        not isinstance(record, dict)
        or set(record) != _FIELDS
        or type(record["format_version"]) is not int
        or record["format_version"] != 1
        or not isinstance(record["status"], str)
        or record["status"] not in {"registered", "registration_failed"}
        or record["direction"] != "reference_to_target"
        or record["coordinate_convention"] != "zero_based_pixel_centres_xy"
        or record["target_sha256"] != target_sha
        or record["reference_sha256"] != reference_sha
        or record["target_size"] != target_size
        or record["reference_size"] != reference_size
        or not isinstance(record["requested_model"], str)
        or record["requested_model"] not in _MODELS
        or not isinstance(record["flags"], list)
        or len(record["flags"]) > 16
        or any(not isinstance(flag, str) or len(flag) > 100 for flag in record["flags"])
    ):
        raise InputRejectedError("registration source, direction, frame or version is invalid")
    # Equality alone would accept bool dimensions as integers.
    for key in ("target_size", "reference_size"):
        if not isinstance(record[key], list) or any(type(n) is not int for n in record[key]):
            raise InputRejectedError("registration dimensions must be integer pixels")
    _scalars(record["quality"], maximum=32)
    _scalars(record["parameters"], maximum=32)
    if record["status"] == "registration_failed":
        if not isinstance(record["quality"].get("reason"), str) or not record["quality"]["reason"]:
            raise InputRejectedError("failed registration must record its explicit reason")
        if (
            record["matrix"] is not None
            or record["model"] is not None
            or record["reference_channels"] is not None
            or record["artifacts"] is not None
        ):
            raise InputRejectedError("failed registration must not contain aligned-image artifacts")
    else:
        matrix = record["matrix"]
        if (
            not isinstance(record["model"], str)
            or record["model"] not in _MODELS
            or type(record["reference_channels"]) is not int
            or record["reference_channels"] not in {1, 3, 4}
            or not isinstance(matrix, list)
            or len(matrix) != 3
            or any(not isinstance(row, list) or len(row) != 3 for row in matrix)
            or any(
                type(n) not in {int, float} or abs(n) > 1e12 or not math.isfinite(n)
                for row in matrix
                for n in row
            )
        ):
            raise InputRejectedError(
                "registered matrix, model or pixel-channel metadata is invalid"
            )
    return cast(dict[str, object], record)


def inverse_coverage(
    matrix: np.ndarray, *, reference_size: tuple[int, int], target_size: tuple[int, int]
) -> np.ndarray:
    """Bounded continuous donor coverage; alpha validity remains producer-measured."""
    for size in (reference_size, target_size):
        if (
            len(size) != 2
            or any(type(n) is not int or n < 1 for n in size)
            or size[0] * size[1] > MAX_REGISTRATION_PIXELS
        ):
            raise InputRejectedError("registration coverage exceeds its admitted working grid")
    if (
        not isinstance(matrix, np.ndarray)
        or matrix.dtype.kind not in "fiu"
        or matrix.shape != (3, 3)
        or not np.isfinite(matrix).all()
        or np.max(np.abs(matrix)) > 1e12
        or abs(matrix[2, 2] - 1) > 1e-8
        or abs(float(np.linalg.det(matrix))) < 1e-12
    ):
        raise InputRejectedError("registration requires an invertible canonical finite matrix")
    inverse = np.linalg.inv(matrix)
    width, height = target_size
    inside = np.zeros((height, width), bool)
    x = np.arange(width, dtype=np.float64)[None, :]
    for start in range(0, height, 128):
        y = np.arange(start, min(start + 128, height), dtype=np.float64)[:, None]
        denominator = inverse[2, 0] * x + inverse[2, 1] * y + inverse[2, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (inverse[0, 0] * x + inverse[0, 1] * y + inverse[0, 2]) / denominator
            v = (inverse[1, 0] * x + inverse[1, 1] * y + inverse[1, 2]) / denominator
        inside[start : start + len(y)] = (
            np.isfinite(u)
            & np.isfinite(v)
            & (u >= 0)
            & (u <= reference_size[0] - 1)
            & (v >= 0)
            & (v <= reference_size[1] - 1)
        )
    return inside


def _values(
    record: dict[str, object],
    matrix: np.ndarray,
    aligned: np.ndarray,
    valid: np.ndarray,
    points: np.ndarray,
) -> None:
    width, height = cast(list[int], record["target_size"])
    reference_dimensions = cast(list[int], record["reference_size"])
    reference_size = (reference_dimensions[0], reference_dimensions[1])
    channels = record["reference_channels"]
    expected = (height, width) if channels == 1 else (height, width, channels)
    if (
        aligned.dtype != np.uint8
        or aligned.shape != expected
        or valid.dtype != np.bool_
        or valid.shape != (height, width)
        or not valid.any()
        or np.any(aligned[~valid] != 0)
        or points.dtype != np.float32
        or points.ndim != 2
        or points.shape[1] != 4
        or not 6 <= points.shape[0] <= 8000
        or not np.isfinite(points).all()
    ):
        raise InputRejectedError(
            "registration aligned pixels, overlap or inliers have invalid types"
        )
    if np.any(
        valid
        & ~inverse_coverage(matrix, reference_size=reference_size, target_size=(width, height))
    ):
        raise InputRejectedError("registration overlap claims pixels outside reference coverage")
    for columns, size in ((points[:, :2], reference_size), (points[:, 2:], (width, height))):
        if np.any(columns < 0) or np.any(columns > np.asarray(size) - 1):
            raise InputRejectedError("registration inlier coordinates escape their source frame")
        if len(np.unique(columns, axis=0)) != len(points):
            raise InputRejectedError("registration inliers repeat physical keypoint coordinates")
    projected = np.column_stack([points[:, :2], np.ones(len(points))]) @ matrix.T
    if np.any(np.abs(projected[:, 2]) < 1e-12):
        raise InputRejectedError("registration inliers cross a projective pole")
    errors = np.linalg.norm(projected[:, :2] / projected[:, 2:] - points[:, 2:], axis=1)
    parameters = cast(dict[str, object], record["parameters"])
    threshold = parameters.get("ransac_threshold")
    quality = cast(dict[str, object], record["quality"])
    rmse = quality.get("reprojection_rmse")
    matched = quality.get("matched_points")
    ratio = quality.get("inlier_ratio")
    minimum = parameters.get("min_inliers")
    minimum_ratio = parameters.get("min_inlier_ratio")
    maximum_rmse = parameters.get("max_rmse")
    if (
        type(matched) is not int
        or not len(points) <= matched <= 8000
        or type(ratio) not in {int, float}
        or abs(cast(float, ratio) - len(points) / matched) > 1e-6
        or type(minimum) is not int
        or not 6 <= minimum <= len(points)
        or type(minimum_ratio) not in {int, float}
        or not 0 < cast(float, minimum_ratio) <= cast(float, ratio)
        or type(maximum_rmse) not in {int, float}
        or not 0 < cast(float, maximum_rmse) <= 10
        or type(rmse) not in {int, float}
        or cast(float, rmse) > cast(float, maximum_rmse)
    ):
        raise InputRejectedError("registration does not meet its recorded support and error gates")
    if (
        type(threshold) not in {int, float}
        or not 0 < cast(float, threshold) <= 10
        or type(quality.get("inliers")) is not int
        or quality["inliers"] != len(points)
        or type(rmse) not in {int, float}
        or np.any(errors > cast(float, threshold) + 1e-5)
        or abs(float(np.sqrt(np.mean(errors**2))) - cast(float, rmse)) > 1e-4
    ):
        raise InputRejectedError("registration quality differs from its actual geometric inliers")


def write_registration(
    ctx: PluginContext,
    *,
    target_geometry: ImageGeometry,
    reference_geometry: ImageGeometry,
    status: str,
    requested_model: str,
    model: str | None,
    matrix: np.ndarray | None,
    aligned_reference: np.ndarray | None,
    overlap_mask: np.ndarray | None,
    inlier_points: np.ndarray | None,
    quality: Mapping[str, object],
    parameters: Mapping[str, object],
    flags: tuple[str, ...] = (),
) -> dict[str, object]:
    """Write a complete source-bound result; failure writes no pixel artifacts."""
    target_sha, target_size = _frame(target_geometry)
    reference_sha, reference_size = _frame(reference_geometry)
    arrays = (matrix, aligned_reference, overlap_mask, inlier_points)
    if any(value is not None and not isinstance(value, np.ndarray) for value in arrays):
        raise InputRejectedError("registration payloads must be numerical arrays or unavailable")
    if status == "registration_failed" and any(value is not None for value in arrays):
        raise InputRejectedError("failed registration must not supply hidden pixel payloads")
    if aligned_reference is not None and aligned_reference.ndim not in {2, 3}:
        raise InputRejectedError("registered reference must use a working image pixel layout")
    channels = (
        None
        if aligned_reference is None
        else 1
        if aligned_reference.ndim == 2
        else aligned_reference.shape[2]
    )
    record: dict[str, object] = {
        "format_version": 1,
        "status": status,
        "direction": "reference_to_target",
        "coordinate_convention": "zero_based_pixel_centres_xy",
        "target_sha256": target_sha,
        "reference_sha256": reference_sha,
        "target_size": target_size,
        "reference_size": reference_size,
        "requested_model": requested_model,
        "model": model,
        "matrix": None if matrix is None else matrix.tolist(),
        "reference_channels": channels,
        "quality": dict(quality),
        "parameters": dict(parameters),
        "flags": list(flags),
        "artifacts": None,
    }
    _metadata(record, target_geometry=target_geometry, reference_geometry=reference_geometry)
    outputs: dict[str, object] = {f"comparison.{name}": None for name in _NAMES}
    if status == "registered":
        if (
            matrix is None
            or aligned_reference is None
            or overlap_mask is None
            or inlier_points is None
        ):
            raise InputRejectedError("registered result requires all measured artifacts")
        _values(record, matrix, aligned_reference, overlap_mask, inlier_points)
        encoded = [encode_npy(a) for a in (aligned_reference, overlap_mask, inlier_points)]
        artifacts = {
            name: ctx.write_artifact(f"{name}.npy", payload, "application/x-npy")
            for name, payload in zip(_NAMES, encoded, strict=True)
        }
        record["artifacts"] = artifacts
        outputs.update({f"comparison.{name}": ref for name, ref in artifacts.items()})
    return {"comparison.transform": record, **outputs}


def read_registration(
    ctx: PluginContext, *, target_geometry: ImageGeometry, reference_geometry: ImageGeometry
) -> RegisteredComparison | None:
    """Read only declared source/frame-verified artifact selectors; failure is unavailable."""
    raw = ctx.inputs.get("comparison.transform")
    if raw is None:
        return None
    record = _metadata(raw, target_geometry=target_geometry, reference_geometry=reference_geometry)
    if record["status"] == "registration_failed":
        return None
    artifacts = record["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != set(_NAMES):
        raise InputRejectedError("registered comparison has missing or unknown artifact companions")
    width, height = cast(list[int], record["target_size"])
    channels = cast(int, record["reference_channels"])
    limits = {
        "aligned_reference": (width * height * channels, 1, 2 if channels == 1 else 3),
        "overlap_mask": (width * height, 1, 2),
        "inlier_points": (8000 * 4, 4, 2),
    }
    decoded = []
    for name in _NAMES:
        value = artifacts[name]
        elements, itemsize, dimensions = limits[name]
        ceiling = elements * itemsize + 10000
        try:
            ref = (
                value
                if isinstance(value, ArtifactRef)
                else ArtifactRef.model_validate(
                    value.get("artifact")
                    if isinstance(value, dict) and set(value) == {"artifact"}
                    else None
                )
            )
        except ValueError as error:
            raise InputRejectedError("registration artifact reference is malformed") from error
        if (
            ref.producer != "comparison.registration"
            or ref.media_type != "application/x-npy"
            or ref.size > ceiling
        ):
            raise InputRejectedError(
                "registration artifacts have invalid ownership, type or budget"
            )
        decoded.append(
            decode_npy(
                ctx.artifact(
                    "comparison.transform", max_bytes=ceiling, selector=("artifacts", name)
                ),
                max_elements=elements,
                max_bytes=ceiling,
                expected_ndim=dimensions,
            )
        )
    aligned, valid, points = decoded
    matrix = np.asarray(record["matrix"], np.float64)
    _values(record, matrix, aligned, valid, points)
    matrix.setflags(write=False)
    return RegisteredComparison(
        matrix,
        aligned,
        valid,
        points,
        cast(str, record["model"]),
        MappingProxyType(cast(dict[str, object], record["quality"])),
    )

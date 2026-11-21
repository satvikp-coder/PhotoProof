"""Source-bound numerical similarity artifacts with explicit measured support.

These contracts describe differences between supplied images, not their provenance.
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import numpy as np

from photoproof.contracts.arrays import decode_npy, encode_npy
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.registration import read_registration

_NAMES = ("dissimilarity_map", "abs_diff_map", "similarity_validity", "edge_suppression")
MAX_PIXELS = 4194304


@dataclass(frozen=True)
class SimilarityMaps:
    dissimilarity: np.ndarray
    abs_difference: np.ndarray
    valid_mask: np.ndarray
    edge_suppressed: np.ndarray


def _ref(value: object) -> ArtifactRef:
    try:
        return (
            value
            if isinstance(value, ArtifactRef)
            else ArtifactRef.model_validate(
                value.get("artifact")
                if isinstance(value, dict) and set(value) == {"artifact"}
                else None
            )
        )
    except ValueError as error:
        raise InputRejectedError("similarity artifact reference is malformed") from error


def _frame(geometry: ImageGeometry) -> tuple[str, list[int]]:
    size = geometry.get("working_size")
    sha = geometry.get("source_sha256")
    if (
        type(geometry.get("schema_version")) is not int
        or geometry.get("schema_version") != 1
        or geometry.get("coordinate_convention") != "zero_based_pixel_centres_xy"
        or not isinstance(sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha)
        or not isinstance(size, list)
        or len(size) != 2
        or any(type(n) is not int or n < 1 for n in size)
        or size[0] * size[1] > MAX_PIXELS
    ):
        raise InputRejectedError("similarity requires bounded source-bound working geometry")
    return sha, size


def _binding(
    record: object, *, target: tuple[str, list[int]], reference: tuple[str, list[int]]
) -> str:
    if not isinstance(record, dict) or record.get("status") != "registered":
        raise InputRejectedError("similarity requires an actual registered transform")
    if (
        type(record.get("format_version")) is not int
        or record.get("format_version") != 1
        or record.get("target_sha256") != target[0]
        or record.get("target_size") != target[1]
        or record.get("reference_sha256") != reference[0]
        or record.get("reference_size") != reference[1]
        or record.get("direction") != "reference_to_target"
        or record.get("coordinate_convention") != "zero_based_pixel_centres_xy"
    ):
        raise InputRejectedError("similarity registration differs from its actual source frames")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "aligned_reference",
        "overlap_mask",
        "inlier_points",
    }:
        raise InputRejectedError("similarity registration companions are missing")
    # Only fixed bounded fields enter this digest; no arbitrary nested metadata.
    matrix = record.get("matrix")
    if (
        not isinstance(matrix, list)
        or len(matrix) != 3
        or any(not isinstance(row, list) or len(row) != 3 for row in matrix)
        or any(
            type(n) not in {int, float} or abs(n) > 1e12 or not math.isfinite(n)
            for row in matrix
            for n in row
        )
    ):
        raise InputRejectedError("similarity registration matrix is invalid")
    hashes = {}
    for name, value in artifacts.items():
        ref = _ref(value)
        if ref.producer != "comparison.registration" or ref.media_type != "application/x-npy":
            raise InputRejectedError("similarity registration artifact ownership is invalid")
        hashes[name] = ref.sha256
    return hashlib.sha256(
        json.dumps(
            {"matrix": matrix, "artifacts": hashes},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _arrays(arrays: tuple[np.ndarray, ...], size: list[int]) -> None:
    width, height = size
    for i, value in enumerate(arrays):
        if (
            not isinstance(value, np.ndarray)
            or value.shape != (height, width)
            or value.dtype != (np.float32 if i < 2 else np.bool_)
        ):
            raise InputRejectedError("similarity array dtype or target-frame shape is invalid")
        if i < 2 and (not np.isfinite(value).all() or np.any((value < 0) | (value > 1))):
            raise InputRejectedError(
                "similarity maps must contain finite unit-interval measurements"
            )
    difference, absolute, valid, suppressed = arrays
    if (
        not valid.any()
        or difference[~valid].any()
        or absolute[~valid].any()
        or suppressed[~valid].any()
    ):
        raise InputRejectedError(
            "similarity contains measurements outside its nonempty valid support"
        )


def _facts(facts: object, arrays: tuple[np.ndarray, ...]) -> None:
    if not isinstance(facts, Mapping):
        raise InputRejectedError("similarity facts must be a numerical-summary mapping")
    score = facts.get("global_ssim")
    measured = float((1 - 2 * arrays[0][arrays[2]]).mean(dtype=np.float64))
    if (
        not isinstance(score, (float, int))
        or isinstance(score, bool)
        or abs(score) > 1
        or not math.isfinite(score)
        or abs(score - measured) > 1e-5
    ):
        raise InputRejectedError("similarity global SSIM contradicts measured dissimilarity")
    if "valid_fraction" in facts and facts["valid_fraction"] != float(arrays[2].mean()):
        raise InputRejectedError("similarity valid fraction contradicts its actual mask")
    if "edge_suppressed_pixels" in facts and facts["edge_suppressed_pixels"] != int(
        arrays[3].sum()
    ):
        raise InputRejectedError("similarity suppression count contradicts its actual mask")


def write_similarity(
    ctx: PluginContext,
    *,
    target_geometry: ImageGeometry,
    reference_geometry: ImageGeometry,
    registration: object,
    dissimilarity: np.ndarray,
    abs_difference: np.ndarray,
    valid_mask: np.ndarray,
    edge_suppressed: np.ndarray,
    facts: Mapping[str, object],
) -> dict[str, object]:
    """Encode all companions before writing, retaining exact upstream alignment binding."""
    target_sha, size = _frame(target_geometry)
    reference_sha, reference_size = _frame(reference_geometry)
    binding = _binding(
        registration, target=(target_sha, size), reference=(reference_sha, reference_size)
    )
    arrays = (dissimilarity, abs_difference, valid_mask, edge_suppressed)
    _arrays(arrays, size)
    _facts(facts, arrays)
    payloads = [encode_npy(value, max_elements=MAX_PIXELS) for value in arrays]
    artifacts = {
        name: ctx.write_artifact(f"{name}.npy", payload, "application/x-npy")
        for name, payload in zip(_NAMES, payloads, strict=True)
    }
    summary = {
        "format_version": 1,
        "status": "measured",
        "target_sha256": target_sha,
        "reference_sha256": reference_sha,
        "target_size": size,
        "reference_size": reference_size,
        "coordinate_convention": "zero_based_pixel_centres_xy",
        "registration_binding": binding,
        "facts": dict(facts),
        "artifacts": artifacts,
    }
    return {
        "comparison.similarity_summary": summary,
        **{f"comparison.{name}": value for name, value in artifacts.items()},
    }


def read_similarity(
    ctx: PluginContext,
    *,
    target_geometry: ImageGeometry,
    reference_geometry: ImageGeometry,
) -> SimilarityMaps | None:
    """Verify sources, upstream binding, companion ownership and bounds before reads."""
    record = ctx.inputs.get("comparison.similarity_summary")
    if record is None:
        return None
    target_sha, size = _frame(target_geometry)
    reference_sha, reference_size = _frame(reference_geometry)
    fields = {
        "format_version",
        "status",
        "target_sha256",
        "reference_sha256",
        "target_size",
        "reference_size",
        "coordinate_convention",
        "registration_binding",
        "facts",
        "artifacts",
    }
    if (
        not isinstance(record, dict)
        or set(record) != fields
        or type(record["format_version"]) is not int
        or record["format_version"] != 1
        or record["status"] != "measured"
        or record["target_sha256"] != target_sha
        or record["reference_sha256"] != reference_sha
        or record["target_size"] != size
        or record["reference_size"] != reference_size
        or record["coordinate_convention"] != "zero_based_pixel_centres_xy"
        or record["registration_binding"]
        != _binding(
            ctx.inputs.get("comparison.transform"),
            target=(target_sha, size),
            reference=(reference_sha, reference_size),
        )
    ):
        raise InputRejectedError(
            "similarity source, frame, schema or registration binding is invalid"
        )
    artifacts = record["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != set(_NAMES):
        raise InputRejectedError("similarity artifact companions are missing")
    arrays = []
    elements = size[0] * size[1]
    # Validate every declared reference before requesting any companion bytes.
    for i, name in enumerate(_NAMES):
        ref = _ref(artifacts[name])
        if (
            ref.producer != "comparison.ssim"
            or ref.media_type != "application/x-npy"
            or ref.size > elements * (4 if i < 2 else 1) + 10000
        ):
            raise InputRejectedError("similarity artifact ownership, type or budget is invalid")
    registered = read_registration(
        ctx, target_geometry=target_geometry, reference_geometry=reference_geometry
    )
    if registered is None:
        raise InputRejectedError("measured similarity has no valid upstream registration")
    for i, name in enumerate(_NAMES):
        ceiling = elements * (4 if i < 2 else 1) + 10000
        arrays.append(
            decode_npy(
                ctx.artifact(
                    "comparison.similarity_summary", selector=("artifacts", name), max_bytes=ceiling
                ),
                max_elements=elements,
                max_bytes=ceiling,
                expected_ndim=2,
            )
        )
    _arrays(tuple(arrays), size)
    _facts(record["facts"], tuple(arrays))
    valid = arrays[2]
    known = registered.overlap_mask
    # Full 11x11 donor support, using bounded integer integral counts rather than
    # allocating a sliding-window tensor. Borders never acquire invented donors.
    integral = (
        np.pad((~known).astype(np.int32), ((1, 0), (1, 0)))
        .cumsum(axis=0, dtype=np.int32)
        .cumsum(axis=1, dtype=np.int32)
    )
    missing = integral[11:, 11:] - integral[:-11, 11:] - integral[11:, :-11] + integral[:-11, :-11]
    if (
        valid[:5].any()
        or valid[-5:].any()
        or valid[:, :5].any()
        or valid[:, -5:].any()
        or np.any(missing[valid[5:-5, 5:-5]] != 0)
    ):
        raise InputRejectedError("similarity validity exceeds fully measured registration support")
    return SimilarityMaps(
        *cast(tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], tuple(arrays))
    )

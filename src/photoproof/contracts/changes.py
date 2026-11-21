"""Bounded typed difference regions bound to actual upstream similarity artifacts."""

import hashlib
import json
import math
import struct
import zlib
from typing import Annotated, Literal, cast

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.similarity import read_similarity


class DifferenceRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    id: int = Field(ge=1, le=4194304)
    bbox: tuple[int, int, int, int]
    area: int = Field(ge=1, le=4194304)
    area_fraction: float = Field(gt=0, le=1)
    mean_dissimilarity: float = Field(ge=0, le=1)
    category: Literal["difference"]

    @field_validator("bbox", mode="before")
    @classmethod
    def coordinates(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class _DifferenceFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    status: Literal["differences_detected", "no_differences_detected"]
    category: Literal["difference"]
    threshold_method: Literal["otsu", "fixed"]
    threshold: float = Field(ge=0, le=1)
    min_threshold: float = Field(ge=0, le=1)
    automatic_threshold: float | None = Field(ge=0, le=1)
    otsu_quantization: Literal["nearest_uint8_divided_by_255"] | None
    min_region_area_fraction: float = Field(gt=0, le=1)
    min_region_area_pixels: int = Field(ge=1, le=4194304)
    kernel_size: int = Field(ge=1, le=9)
    cleanup: Literal["elliptical_open_then_close_clipped_to_validity"]
    initial_threshold_pixels: int = Field(ge=0, le=4194304)
    changed_pixels: int = Field(ge=0, le=4194304)
    measured_pixels: int = Field(ge=1, le=4194304)
    target_pixels: int = Field(ge=1, le=4194304)
    region_count: int = Field(ge=0, le=4194304)
    reported_region_count: int = Field(ge=0, le=1000)
    regions_truncated: bool
    removed_small_components: int = Field(ge=0, le=4194304)
    low_specificity: bool
    interpretation: Literal[
        "measured differences between supplied inputs at configured sensitivity"
    ]
    limitations: list[
        Literal[
            "differences_do_not_establish_manipulation",
            "registration_error",
            "compression_and_threshold_sensitivity",
        ]
    ] = Field(max_length=3)


class DifferenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    format_version: Literal[1]
    coordinate_convention: Literal["zero_based_pixel_centres_xy"]
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_size: tuple[
        Annotated[int, Field(strict=True, ge=1)], Annotated[int, Field(strict=True, ge=1)]
    ]
    similarity_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    regions: tuple[DifferenceRegion, ...] = Field(max_length=1000)
    facts: dict[str, object]
    mask: ArtifactRef
    labels: ArtifactRef

    @field_validator("facts", mode="before")
    @classmethod
    def numerical_facts(cls, value: object) -> object:
        return _DifferenceFacts.model_validate(value).model_dump(mode="json")

    @field_validator("format_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("change format_version must be an exact integer")
        return value

    @field_validator("mask", "labels", mode="before")
    @classmethod
    def artifact(cls, value: object) -> object:
        if isinstance(value, dict) and set(value) == {"artifact"}:
            return value["artifact"]
        return value


def encode_change_mask(mask: np.ndarray) -> bytes:
    """Encode only bounded derived boolean pixels as minimal unfiltered L8 PNG."""
    if (
        not isinstance(mask, np.ndarray)
        or mask.dtype != np.bool_
        or mask.ndim != 2
        or not mask.size
        or mask.size > 4194304
    ):
        raise InputRejectedError("change PNG requires bounded nonempty boolean pixels")
    height, width = mask.shape
    rows = np.zeros((height, width + 1), np.uint8)
    rows[:, 1:] = mask.astype(np.uint8) * 255

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows.tobytes()))
        + chunk(b"IEND", b"")
    )


def _verify_png(data: bytes, labels: np.ndarray) -> None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise InputRejectedError("change mask is not a derived PNG")
    position = 8
    chunks = []
    for expected in (b"IHDR", b"IDAT", b"IEND"):
        if position + 12 > len(data):
            raise InputRejectedError("change PNG is truncated")
        length = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        end = position + 8 + length
        if kind != expected or end + 4 > len(data):
            raise InputRejectedError("change PNG chunks differ from the fixed derived schema")
        payload = data[position + 8 : end]
        if zlib.crc32(kind + payload) != struct.unpack(">I", data[end : end + 4])[0]:
            raise InputRejectedError("change PNG checksum mismatch")
        chunks.append(payload)
        position = end + 4
    height, width = labels.shape
    if (
        position != len(data)
        or chunks[0] != struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
        or chunks[2]
    ):
        raise InputRejectedError("change PNG dimensions, mode or trailing data are invalid")
    limit = height * (width + 1)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(chunks[1], limit + 1)
    except zlib.error as error:
        raise InputRejectedError("change PNG compression is invalid") from error
    if len(raw) != limit or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise InputRejectedError("change PNG exceeds its exact bounded pixel payload")
    rows = np.frombuffer(raw, np.uint8).reshape(height, width + 1)
    if rows[:, 0].any() or not np.array_equal(rows[:, 1:], (labels > 0).astype(np.uint8) * 255):
        raise InputRejectedError("change PNG pixels contradict the actual label raster")


def similarity_binding(summary: object) -> str:
    """Digest a fixed set of actual upstream typed companion identities."""
    if not isinstance(summary, dict) or summary.get("status") != "measured":
        raise InputRejectedError("change regions require measured upstream similarity")
    artifacts = summary.get("artifacts")
    names = {"dissimilarity_map", "abs_diff_map", "similarity_validity", "edge_suppression"}
    if not isinstance(artifacts, dict) or set(artifacts) != names:
        raise InputRejectedError("change similarity companions are missing")
    hashes = {}
    for name, value in artifacts.items():
        try:
            ref = (
                value
                if isinstance(value, ArtifactRef)
                else ArtifactRef.model_validate(
                    value.get("artifact") if isinstance(value, dict) else None
                )
            )
        except ValueError as error:
            raise InputRejectedError("change similarity reference is malformed") from error
        if ref.producer != "comparison.ssim" or ref.media_type != "application/x-npy":
            raise InputRejectedError("change similarity ownership or media is invalid")
        hashes[name] = ref.sha256
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_changes(
    ctx: PluginContext,
    *,
    target_geometry: ImageGeometry,
    reference_geometry: ImageGeometry,
) -> tuple[DifferenceRecord, np.ndarray] | None:
    """Verify actual measured support and region statistics before returning labels."""
    raw = ctx.inputs.get("comparison.change_regions")
    if raw is None:
        return None
    try:
        record = DifferenceRecord.model_validate(raw)
    except ValidationError as error:
        raise InputRejectedError("change-region schema is malformed") from error
    width, height = record.target_size
    elements = width * height
    if (
        elements > 4194304
        or list(record.target_size) != target_geometry.get("working_size")
        or record.target_sha256 != target_geometry.get("source_sha256")
        or record.reference_sha256 != reference_geometry.get("source_sha256")
        or record.similarity_binding
        != similarity_binding(ctx.inputs.get("comparison.similarity_summary"))
        or record.labels.producer != "comparison.change_mask"
        or record.labels.media_type != "application/x-npy"
        or record.labels.size > elements * 4 + 10000
        or record.mask.producer != "comparison.change_mask"
        or record.mask.media_type != "image/png"
        or record.mask.size > elements * 2 + height + 10000
    ):
        raise InputRejectedError("change source, frame, upstream or companion bounds are invalid")
    similarity = read_similarity(
        ctx, target_geometry=target_geometry, reference_geometry=reference_geometry
    )
    if similarity is None:
        raise InputRejectedError("change regions have no measured similarity")
    ceiling = elements * 4 + 10000
    labels = decode_npy(
        ctx.artifact("comparison.change_regions", selector=("labels",), max_bytes=ceiling),
        max_bytes=ceiling,
        max_elements=elements,
        expected_ndim=2,
    )
    if (
        labels.dtype != np.int32
        or labels.shape != (height, width)
        or labels.min() < 0
        or labels.max() > elements
        or labels[~similarity.valid_mask].any()
    ):
        raise InputRejectedError("change labels have invalid type, shape, identifiers or support")
    counts = np.bincount(labels.ravel(), minlength=int(labels.max()) + 1)
    sums = np.bincount(
        labels.ravel(), weights=similarity.dissimilarity.ravel(), minlength=len(counts)
    )
    positive_ids = np.flatnonzero(counts[1:]) + 1
    changed = int(np.count_nonzero(labels))
    facts = record.facts
    minimum = math.ceil(cast(float, facts["min_region_area_fraction"]) * elements)
    if (
        facts["min_region_area_pixels"] != max(1, minimum)
        or cast(int, facts["kernel_size"]) % 2 == 0
        or any(counts[i] < minimum for i in positive_ids)
        or (
            facts["threshold_method"] == "fixed"
            and (facts["automatic_threshold"] is not None or facts["otsu_quantization"] is not None)
        )
        or (
            facts["threshold_method"] == "otsu"
            and (
                facts["automatic_threshold"] is None
                or facts["otsu_quantization"] is None
                or facts["threshold"]
                != max(
                    cast(float, facts["automatic_threshold"]), cast(float, facts["min_threshold"])
                )
            )
        )
    ):
        raise InputRejectedError("change sensitivity or cleanup metadata is inconsistent")
    for name, expected in {
        "changed_pixels": changed,
        "region_count": len(positive_ids),
        "reported_region_count": len(record.regions),
        "target_pixels": elements,
        "measured_pixels": int(similarity.valid_mask.sum()),
    }.items():
        if type(facts.get(name)) is not int or facts[name] != expected:
            raise InputRejectedError("change-region counts contradict actual measured pixels")
    if (
        facts.get("changed_pixels") != changed
        or facts.get("region_count") != len(positive_ids)
        or facts.get("reported_region_count") != len(record.regions)
        or facts.get("regions_truncated") != (len(record.regions) < len(positive_ids))
        or facts.get("status") != ("differences_detected" if changed else "no_differences_detected")
        or facts.get("category") != "difference"
        or len({r.id for r in record.regions}) != len(record.regions)
    ):
        raise InputRejectedError("change-region summary contradicts its actual labels")
    if (
        type(facts.get("regions_truncated")) is not bool
        or type(facts.get("low_specificity")) is not bool
        or facts["low_specificity"] != (changed / int(similarity.valid_mask.sum()) >= 0.8)
    ):
        raise InputRejectedError("change-region flags contradict actual measured support")
    # Aggregate true extrema once, in bounded row chunks. Avoid rescanning the full
    # image for each region, including nested or overlapping bounding boxes.
    min_x = np.full(len(counts), width, np.int32)
    min_y = np.full(len(counts), height, np.int32)
    max_x = np.full(len(counts), -1, np.int32)
    max_y = np.full(len(counts), -1, np.int32)
    for start in range(0, height, 64):
        end = min(start + 64, height)
        ids = labels[start:end].ravel()
        xs = np.tile(np.arange(width, dtype=np.int32), end - start)
        ys = np.repeat(np.arange(start, end, dtype=np.int32), width)
        np.minimum.at(min_x, ids, xs)
        np.maximum.at(max_x, ids, xs)
        np.minimum.at(min_y, ids, ys)
        np.maximum.at(max_y, ids, ys)
    for region in record.regions:
        i = region.id
        if (
            i >= len(counts)
            or region.area != counts[i]
            or region.bbox
            != (
                int(min_x[i]),
                int(min_y[i]),
                int(max_x[i] - min_x[i] + 1),
                int(max_y[i] - min_y[i] + 1),
            )
            or abs(region.area_fraction - region.area / elements) > 1e-12
            or abs(region.mean_dissimilarity - sums[i] / counts[i]) > 1e-5
        ):
            raise InputRejectedError(
                "change region area, bounds or mean contradicts measured pixels"
            )
    _verify_png(
        ctx.artifact(
            "comparison.change_regions", selector=("mask",), max_bytes=elements * 2 + height + 10000
        ),
        labels,
    )
    return record, labels

"""Read verified dense copy-move artifacts without inventing localization or reliability."""

import json
from hashlib import sha256
from typing import Literal

import numpy as np

from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.copymove import Clusters, Mask, VerificationBindings, Verified
from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.evidence_io import SpatialEvidence
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.grids import SamplingGrid, decoded_from_working
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext

_REQUIRED = (
    "copymove.mask",
    "copymove.mask_metadata",
    "copymove.correlation_map",
    "copymove.correlation_validity",
    "copymove.regions",
    "copymove.verification_bindings",
    "copymove.verified",
    "copymove.verified_inliers",
)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputRejectedError("dense mask manifest repeats a field")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise InputRejectedError(f"dense mask manifest has nonfinite constant {value}")


def _text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeError as error:
        raise InputRejectedError("dense mask metadata must be UTF-8 JSON") from error


def read_copy_move(
    ctx: PluginContext,
    *,
    geometry: ImageGeometry,
    strength_class: Literal["weak", "medium", "strong"] = "strong",
    minimum_geometric_support: float = 0.8,
) -> SpatialEvidence | None:
    """Return actual source-bound decoded-pixel evidence, or None if unavailable.

    Verification scores and pixel correlation are uncalibrated heuristics. Any
    upstream caution or weak geometric support downgrades this aggregate layer;
    a binary mask alone cannot authorize strong evidence.
    """
    if not all(name in ctx.inputs for name in _REQUIRED):
        return None
    if (
        not np.isfinite(minimum_geometric_support)
        or not 0 <= minimum_geometric_support <= 1
        or strength_class not in {"weak", "medium", "strong"}
    ):
        raise InputRejectedError(
            "copy-move normalization needs bounded documented support controls"
        )
    manifest_bytes = ctx.artifact("copymove.regions", max_bytes=1 << 20)
    try:
        manifest = json.loads(
            _text(manifest_bytes), object_pairs_hook=_unique, parse_constant=_constant
        )
    except (ValueError, TypeError, RecursionError) as error:
        raise InputRejectedError("dense mask manifest is invalid bounded JSON") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "photoproof.copymove.dense-regions"
        or type(manifest.get("format_version")) is not int
        or manifest["format_version"] != 1
        or manifest.get("source_sha256") != geometry["source_sha256"]
        or manifest.get("image_size") != geometry["working_size"]
        or manifest.get("coordinate_space") != "working_pixels"
        or manifest.get("calibration") != "uncalibrated"
        or manifest.get("boundary_convention") != "pixel_centers_xy"
        or manifest.get("raster_authority") != "mask_png"
        or manifest.get("correlation_range") != [-1, 1]
        or manifest.get("invalid_storage_value") != 0
    ):
        raise InputRejectedError(
            "dense mask manifest source, frame, format or calibration is invalid"
        )

    def bound(name: str, digest: str, *, ceiling: int = 64 << 20) -> bytes:
        payload = ctx.artifact(name, max_bytes=ceiling)
        if manifest.get(digest) != sha256(payload).hexdigest():
            raise InputRejectedError(f"dense mask {name} differs from its bound manifest")
        return payload

    source = geometry["source_sha256"]
    bindings = VerificationBindings.from_json(
        _text(
            bound(
                "copymove.verification_bindings",
                "verification_bindings_sha256",
                ceiling=1 << 20,
            )
        ),
        source_sha256=source,
    )
    payload = ctx.artifact("copymove.verified")
    bindings.verify_payload("verified", payload)
    verified = Verified.from_npz(payload, source_sha256=source)
    payload = ctx.artifact("copymove.verified_inliers", max_bytes=1 << 20)
    bindings.verify_payload("inliers", payload)
    inliers = Clusters.from_json(_text(payload), source_sha256=source)
    groups = {c.id: c for c in inliers.clusters}
    if (
        bindings.image_size != verified.image_size
        or bindings.image_size != inliers.image_size
        or list(bindings.image_size) != geometry["working_size"]
        or set(groups) != {t.cluster_id for t in verified.transforms}
        or any(
            len(groups[t.cluster_id].pair_indices) != t.inliers or t.inliers < bindings.min_inliers
            for t in verified.transforms
        )
    ):
        raise InputRejectedError("dense mask verified support or source-frame provenance disagrees")
    metadata = _text(bound("copymove.mask_metadata", "mask_metadata_sha256", ceiling=1 << 20))
    mask = Mask.from_png(
        bound("copymove.mask", "mask_sha256"), metadata=metadata, source_sha256=source
    )
    values = decode_npy(bound("copymove.correlation_map", "correlation_sha256"), expected_ndim=2)
    valid = decode_npy(bound("copymove.correlation_validity", "validity_sha256"), expected_ndim=2)
    if (
        mask.image_size != bindings.image_size
        or values.shape != mask.mask.shape
        or values.dtype != np.float32
        or valid.dtype != np.bool_
        or valid.shape != values.shape
        or not np.isfinite(values).all()
        or np.any(np.abs(values) > 1)
        or np.any(values[~valid] != 0)
        or np.any((mask.mask != 0) & ~valid)
        or (np.any(mask.mask) and not verified.transforms)
    ):
        raise InputRejectedError(
            "dense mask correlation, validity or verified support is inconsistent"
        )
    if not valid.any() or not verified.transforms:
        return None
    quality = min(t.score for t in verified.transforms)
    marker = "ransac_" + bindings.geometric_model
    informational = {marker, "duplicate_coordinates_removed"}
    cautions = sorted(
        {flag for t in verified.transforms for flag in t.flags if flag not in informational}
    )
    if any(marker not in t.flags for t in verified.transforms):
        cautions.append("missing_algorithm_marker")
    strength = "weak" if cautions or quality < minimum_geometric_support else strength_class
    scores = np.where(mask.mask != 0, np.maximum(values, 0) * quality, 0).astype(np.float32)
    decoded, measured = decoded_from_working(scores, valid, geometry=geometry)
    size = geometry["decoded_size"]
    notes = (
        "verified_dense_pixel_similarity",
        "original_source_bound",
        "exact_exif_inverse",
        "localized_search_scope",
        "uncalibrated_geometric_quality_times_positive_zncc",
        "mask_sha256:" + str(manifest["mask_sha256"]),
        "verification_bindings_sha256:" + str(manifest["verification_bindings_sha256"]),
        *(
            ("reliability_downgraded_to_weak",)
            if strength == "weak" and strength_class != "weak"
            else ()
        ),
        *(("upstream_cautions_present",) if cautions else ()),
    )
    return SpatialEvidence(
        EvidenceLayer("copymove.mask", decoded, (), strength, measured, "uncalibrated", notes),
        source,
        SamplingGrid((size[0], size[1]), (0, 0), (1, 1), (1, 1)),
    )

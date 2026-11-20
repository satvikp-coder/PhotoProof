"""Artifact-backed dense masks restricted to bound, geometrically verified inlier rows."""

import json
from hashlib import sha256

from photoproof.contracts.arrays import encode_npy
from photoproof.contracts.copymove import (
    Clusters,
    Keypoints,
    Matches,
    VerificationBindings,
    Verified,
)
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.mask.localization import localize_verified
from photoproof.plugins.copymove.mask.regions import polygons
from photoproof.plugins.copymove.mask.schema import Config


def _text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeError as error:
        raise InputRejectedError("mask metadata must be UTF-8 JSON") from error


def run(ctx: PluginContext) -> dict[str, object]:
    """Publish working-pixel labels, signed correlation, explicit validity, and polygons."""
    geometry = ctx.inputs.get("image.geometry")
    if not isinstance(geometry, dict) or not isinstance(geometry.get("source_sha256"), str):
        raise InputRejectedError("mask requires authoritative source-bound working geometry")
    source = geometry["source_sha256"]
    binding_bytes = ctx.artifact("copymove.verification_bindings", max_bytes=1 << 20)
    bindings = VerificationBindings.from_json(_text(binding_bytes), source_sha256=source)
    if list(bindings.image_size) != geometry.get("working_size"):
        raise InputRejectedError("mask bindings differ from host working geometry")
    payload = ctx.artifact("copymove.keypoints")
    bindings.verify_payload("keypoints", payload)
    features = Keypoints.from_npz(payload, source_sha256=source)
    payload = ctx.artifact("copymove.matches")
    bindings.verify_payload("matches", payload)
    matches = Matches.from_npz(payload, source_sha256=source)
    payload = ctx.artifact("copymove.clusters", max_bytes=1 << 20)
    bindings.verify_payload("clusters", payload)
    candidates = Clusters.from_json(_text(payload), source_sha256=source)
    payload = ctx.artifact("copymove.verified")
    bindings.verify_payload("verified", payload)
    verified = Verified.from_npz(payload, source_sha256=source)
    payload = ctx.artifact("copymove.verified_inliers", max_bytes=1 << 20)
    bindings.verify_payload("inliers", payload)
    inliers = Clusters.from_json(_text(payload), source_sha256=source)
    del payload
    parents = {c.id: c for c in candidates.clusters}
    if (
        candidates.image_size != bindings.image_size
        or candidates.num_matches != len(matches.pairs)
        or set(parents) != set(verified.cluster_ids)
        or any(
            c.id not in parents or not set(c.pair_indices) <= set(parents[c.id].pair_indices)
            for c in inliers.clusters
        )
    ):
        raise InputRejectedError(
            "mask inlier rows are not a subset of the bound candidate clusters"
        )
    config = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )
    result = localize_verified(
        features,
        matches,
        verified,
        inliers,
        image=ctx.image("image.working"),
        reproj_threshold=bindings.reproj_threshold,
        min_inliers=bindings.min_inliers,
        config=config,
    )
    # Prepare and validate every encoding before publishing any output references.
    mask_bytes = result.mask.to_png()
    metadata_bytes = result.mask.to_json().encode("utf-8")
    correlation_bytes = encode_npy(result.correlation.values)
    validity_bytes = encode_npy(result.correlation.valid)
    regions_bytes = json.dumps(
        {
            "schema": "photoproof.copymove.dense-regions",
            "format_version": 1,
            "source_sha256": source,
            "image_size": list(bindings.image_size),
            "coordinate_space": "working_pixels",
            "calibration": "uncalibrated",
            "boundary_convention": "pixel_centers_xy",
            "raster_authority": "mask_png",
            "mask_sha256": sha256(mask_bytes).hexdigest(),
            "mask_metadata_sha256": sha256(metadata_bytes).hexdigest(),
            "correlation_sha256": sha256(correlation_bytes).hexdigest(),
            "validity_sha256": sha256(validity_bytes).hexdigest(),
            "verification_bindings_sha256": sha256(binding_bytes).hexdigest(),
            "correlation_range": [-1, 1],
            "invalid_storage_value": 0,
            "regions": polygons(result.mask),
            "summary": result.summary,
            "verification_flags": {str(t.cluster_id): list(t.flags) for t in verified.transforms},
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(regions_bytes) > 1 << 20:
        raise InputRejectedError("mask polygon metadata exceeds one MiB; no partial result")
    mask_ref = ctx.write_artifact("mask.png", mask_bytes, "image/png")
    metadata_ref = ctx.write_artifact("mask-metadata.json", metadata_bytes, "application/json")
    correlation_ref = ctx.write_artifact("correlation.npy", correlation_bytes, "application/x-npy")
    validity_ref = ctx.write_artifact("correlation-valid.npy", validity_bytes, "application/x-npy")
    regions_ref = ctx.write_artifact("regions.json", regions_bytes, "application/json")
    return {
        "copymove.mask": mask_ref,
        "copymove.mask_metadata": metadata_ref,
        "copymove.correlation_map": correlation_ref,
        "copymove.correlation_validity": validity_ref,
        "copymove.regions": regions_ref,
        "copymove.mask_stats": {
            **result.summary,
            "source_sha256": source,
            "image_size": list(bindings.image_size),
            "coordinate_space": "working_pixels",
            "regions_artifact": regions_ref,
        },
    }

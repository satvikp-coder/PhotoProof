"""Verify actual artifact-backed hypotheses and publish traceable inlier identities."""

import json
from hashlib import sha256

from photoproof.contracts.copymove import Clusters, Keypoints, Matches, VerificationBindings
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.verification.algorithm import verify_clusters
from photoproof.plugins.copymove.verification.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Publish geometry-only verification, not a forgery or authenticity verdict."""
    geometry = ctx.inputs.get("image.geometry")
    if not isinstance(geometry, dict) or not isinstance(geometry.get("source_sha256"), str):
        raise InputRejectedError(
            "verification requires authoritative source-bound working geometry"
        )
    source = geometry["source_sha256"]
    payload = ctx.artifact("copymove.keypoints")
    keypoints_sha = sha256(payload).hexdigest()
    features = Keypoints.from_npz(payload, source_sha256=source)
    payload = ctx.artifact("copymove.matches")
    matches_sha = sha256(payload).hexdigest()
    matches = Matches.from_npz(payload, source_sha256=source)
    payload = ctx.artifact("copymove.clusters", max_bytes=1 << 20)
    clusters_sha = sha256(payload).hexdigest()
    try:
        cluster_text = payload.decode("utf-8")
    except UnicodeError as error:
        raise InputRejectedError("verification cluster artifact is not UTF-8 JSON") from error
    clusters = Clusters.from_json(cluster_text, source_sha256=source)
    del payload
    if list(features.image_size) != geometry.get("working_size"):
        raise InputRejectedError("verification features differ from the host working frame")
    config = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )
    result = verify_clusters(
        features, matches, clusters, image=ctx.image("image.working"), seed=ctx.seed, config=config
    )
    verified_bytes = result.verified.to_npz()
    inlier_bytes = result.inliers.to_json().encode("utf-8")
    bindings = VerificationBindings(
        source_sha256=source,
        image_size=features.image_size,
        keypoints_sha256=keypoints_sha,
        matches_sha256=matches_sha,
        clusters_sha256=clusters_sha,
        verified_sha256=sha256(verified_bytes).hexdigest(),
        inliers_sha256=sha256(inlier_bytes).hexdigest(),
        geometric_model=config.model,
        reproj_threshold=config.reproj_threshold,
        min_inliers=config.min_inliers,
        seed=ctx.seed,
        ransac_stop_confidence=config.ransac_stop_confidence,
    )
    binding_bytes = bindings.to_json().encode("utf-8")
    detail_bytes = json.dumps(
        {
            "schema": "photoproof.copymove.verification-details",
            "format_version": 1,
            "source_sha256": source,
            "image_size": list(features.image_size),
            "coordinate_space": "working_pixels",
            "calibration": "uncalibrated",
            "seed": ctx.seed,
            "bindings_sha256": sha256(binding_bytes).hexdigest(),
            "clusters": result.details,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(detail_bytes) > 1 << 20:
        raise InputRejectedError("verification details exceed the one-MiB metadata ceiling")
    verified = ctx.write_artifact("verified.npz", verified_bytes, "application/x-npz")
    inliers = ctx.write_artifact("verified-inliers.json", inlier_bytes, "application/json")
    details = ctx.write_artifact("verification-details.json", detail_bytes, "application/json")
    binding_ref = ctx.write_artifact(
        "verification-bindings.json", binding_bytes, "application/json"
    )
    summary = {
        **result.summary,
        "source_sha256": source,
        "image_size": list(features.image_size),
        "coordinate_space": "working_pixels",
        "contract_version": result.verified.contract_version,
        "verified_artifact": verified,
        "inliers_artifact": inliers,
        "details_artifact": details,
        "bindings_artifact": binding_ref,
    }
    return {
        "copymove.verified": verified,
        "copymove.verified_inliers": inliers,
        "copymove.verification_bindings": binding_ref,
        "copymove.verification_details": details,
        "copymove.verification_stats": summary,
    }

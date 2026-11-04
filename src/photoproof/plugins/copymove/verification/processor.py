"""Processor wiring for the verification plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.copymove.clustering.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import json

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.clustering.algorithm import (
    analyze_copy_move,
)
from photoproof.plugins.copymove.clustering.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run copy-move verification on decoded image and keypoints.

    Reads ``copymove.matches`` (NPZ artifact with pairs and distances) and
    ``copymove.keypoints`` (NPZ artifact with keypoints), and returns
    ``copymove.verification`` (verification result artifact) and
    ``copymove.match_stats`` (summary statistics).
    """
    if "copymove.matches" not in ctx.inputs:
        raise NotApplicableError(
            "copymove.verification requires copymove.matches; not declared in inputs"
        )
    if "copymove.keypoints" not in ctx.inputs:
        raise NotApplicableError(
            "copymove.verification requires copymove.keypoints; not declared in inputs"
        )

    matches_data = ctx.inputs["copymove.matches"]
    keypoints_data = ctx.inputs["copymove.keypoints"]

    if not isinstance(matches_data, dict):
        raise InputRejectedError(
            "copymove.verification: copymove.matches must be a dict, "
            f"got {type(matches_data).__name__}"
        )
    if not isinstance(keypoints_data, dict):
        raise InputRejectedError(
            "copymove.verification: copymove.keypoints must be a dict, "
            f"got {type(keypoints_data).__name__}"
        )

    if "pairs" not in matches_data or "distances" not in matches_data:
        raise InputRejectedError(
            "copymove.verification: copymove.matches must contain 'pairs' and 'distances' keys"
        )

    pairs = np.array(matches_data["pairs"], dtype=np.int32)
    _distances = np.array(matches_data["distances"], dtype=np.float32)
    keypoints = np.array(keypoints_data["keypoints"], dtype=np.float32)

    config = ctx.config if isinstance(ctx.config, Config) else None
    model = getattr(config, "model", "similarity") if config else "similarity"
    reproj_threshold = getattr(config, "reproj_threshold", 3.0) if config else 3.0
    min_inliers = getattr(config, "min_inliers", 6) if config else 6
    max_iterations = getattr(config, "max_iterations", 1000) if config else 1000
    scale_min = getattr(config, "scale_min", 0.5) if config else 0.5
    scale_max = getattr(config, "scale_max", 2.0) if config else 2.0
    max_shear_deg = getattr(config, "max_shear_deg", 45.0) if config else 45.0

    result = analyze_copy_move(
        keypoints,
        pairs,
        model=model,
        reproj_threshold=reproj_threshold,
        min_inliers=min_inliers,
        max_iterations=max_iterations,
        scale_min=scale_min,
        scale_max=scale_max,
        max_shear_deg=max_shear_deg,
    )

    # Write verification result as JSON artifact
    verification_json = json.dumps(
        {
            "cluster_id": result["cluster_id"],
            "matrix": result["matrix"].tolist(),
            "inliers": result["inliers"],
            "inlier_ratio": result["inlier_ratio"],
            "rmse": result["rmse"],
            "scale": result["scale"],
            "rotation_deg": result["rotation_deg"],
            "score": result["score"],
            "model": result["model"],
            "reason": result["reason"],
        },
        indent=2,
    )
    verification_artifact_ref = ctx.write_artifact(
        "verification.json", verification_json.encode("utf-8"), "application/json"
    )

    summary = {
        "cluster_id": result["cluster_id"],
        "inliers": result["inliers"],
        "inlier_ratio": result["inlier_ratio"],
        "rmse": result["rmse"],
        "scale": result["scale"],
        "rotation_deg": result["rotation_deg"],
        "score": result["score"],
        "model": result["model"],
        "reason": result["reason"],
        "verification_artifact": verification_artifact_ref,
    }

    return {
        "copymove.verification": verification_artifact_ref,
    }

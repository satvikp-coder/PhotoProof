"""Processor wiring for the SIFT descriptor matching plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.copymove.matching.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import io

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.matching.algorithm import (
    analyze_sift_matching,
    sift_matching_result_to_dict,
)
from photoproof.plugins.copymove.sift_keypoints.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run SIFT descriptor matching on keypoints.

    Reads ``copymove.keypoints`` (NPZ artifact with keypoints and descriptors)
    and returns ``copymove.matches`` (NPZ artifact) and
    ``copymove.keypoint_stats`` (summary statistics).
    """
    if "copymove.keypoints" not in ctx.inputs:
        raise NotApplicableError(
            "copymove.matching requires copymove.keypoints; not declared in inputs"
        )

    keypoints_data = ctx.inputs["copymove.keypoints"]
    if not isinstance(keypoints_data, dict):
        raise InputRejectedError(
            "copymove.matching: copymove.keypoints must be a dict, "
            f"got {type(keypoints_data).__name__}"
        )

    if "keypoints" not in keypoints_data or "descriptors" not in keypoints_data:
        raise InputRejectedError(
            "copymove.matching: copymove.keypoints must contain 'keypoints' and 'descriptors' keys"
        )

    keypoints = np.array(keypoints_data["keypoints"], dtype=np.float32)
    descriptors = np.array(keypoints_data["descriptors"], dtype=np.float32)

    config = ctx.config if isinstance(ctx.config, Config) else None
    max_keypoints = getattr(config, "max_keypoints", 20000) if config else 20000
    ratio = getattr(config, "ratio", 0.5) if config else 0.5
    min_spatial_distance = getattr(config, "min_spatial_distance", 10.0) if config else 10.0
    max_pairs = getattr(config, "max_pairs", 50000) if config else 50000

    result = analyze_sift_matching(
        keypoints,
        descriptors,
        k=max_keypoints,
        ratio=ratio,
        min_spatial_distance=min_spatial_distance,
        max_pairs=max_pairs,
    )

    # Write matches as NPZ artifact
    buffer = io.BytesIO()
    np.savez(
        buffer,
        pairs=result["pairs"],
        distances=result["distances"],
    )
    matches_artifact_ref = ctx.write_artifact("matches.npz", buffer.getvalue(), "application/x-npy")

    summary = sift_matching_result_to_dict(result)
    summary["keypoints_artifact"] = matches_artifact_ref
    summary["descriptors_artifact"] = matches_artifact_ref

    return {
        "copymove.matches": matches_artifact_ref,
        "copymove.match_stats": summary,
    }

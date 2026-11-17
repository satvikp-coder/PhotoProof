"""Processor wiring for the copy-move mask plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.copymove.mask.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import io

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.mask.algorithm import analyze_copy_move_mask
from photoproof.plugins.copymove.mask.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run copy-move mask generation on verified matches.

    Reads ``copymove.matches`` (NPZ artifact with pairs and distances),
    ``copymove.keypoints`` (NPZ artifact with keypoints), and
    ``image.decoded`` (decoded image array), and returns
    ``copymove.mask`` (NPZ artifact with source/target masks) and
    ``copymove.mask_stats`` (summary statistics).
    """
    if "copymove.matches" not in ctx.inputs:
        raise NotApplicableError("copymove.mask requires copymove.matches; not declared in inputs")
    if "copymove.keypoints" not in ctx.inputs:
        raise NotApplicableError(
            "copymove.mask requires copymove.keypoints; not declared in inputs"
        )
    if "image.decoded" not in ctx.inputs:
        raise NotApplicableError("copymove.mask requires image.decoded; not declared in inputs")

    matches_data = ctx.inputs["copymove.matches"]
    keypoints_data = ctx.inputs["copymove.keypoints"]
    image_data = ctx.inputs["image.decoded"]

    if not isinstance(matches_data, dict):
        raise InputRejectedError(
            f"copymove.mask: copymove.matches must be a dict, got {type(matches_data).__name__}"
        )
    if not isinstance(keypoints_data, dict):
        raise InputRejectedError(
            f"copymove.mask: copymove.keypoints must be a dict, got {type(keypoints_data).__name__}"
        )
    if not isinstance(image_data, np.ndarray):
        raise InputRejectedError(
            f"copymove.mask: image.decoded must be a NumPy array, got {type(image_data).__name__}"
        )

    pairs = np.array(matches_data["pairs"], dtype=np.int32)
    keypoints = np.array(keypoints_data["keypoints"], dtype=np.float32)
    image = image_data

    config = ctx.config if isinstance(ctx.config, Config) else None
    window_size = getattr(config, "window_size", 11) if config else 11
    threshold = getattr(config, "threshold", 0.5) if config else 0.5
    min_cluster_size = getattr(config, "min_cluster_size", 5) if config else 5

    result = analyze_copy_move_mask(
        keypoints,
        pairs,
        image,
        window_size=window_size,
        threshold=threshold,
        min_cluster_size=min_cluster_size,
    )

    # Write mask as NPZ artifact
    buffer = io.BytesIO()
    np.savez(
        buffer,
        mask=result["mask"],
        source_mask=result["source_mask"],
        target_mask=result["target_mask"],
    )
    mask_artifact_ref = ctx.write_artifact("mask.npz", buffer.getvalue(), "application/x-npy")

    summary = {
        "mask_artifact": mask_artifact_ref,
        "num_clusters": result["num_clusters"],
        "method": result["method"],
        "config": result["config"],
    }

    return {
        "copymove.mask": mask_artifact_ref,
        "copymove.mask_stats": summary,
    }

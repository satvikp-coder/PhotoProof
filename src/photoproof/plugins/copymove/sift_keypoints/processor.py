"""Processor wiring for the SIFT keypoint plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.copymove.sift_keypoints.algorithm` and returns typed
outputs matching ``METADATA.outputs``.
"""

from __future__ import annotations

import io

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.sift_keypoints.algorithm import (
    analyze_sift_keypoints,
    keypoints_result_to_dict,
)
from photoproof.plugins.copymove.sift_keypoints.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run SIFT keypoint extraction on the working image.

    Reads ``image.working`` (grayscale or RGB working image) and returns
    ``copymove.keypoints`` (NPZ artifact with keypoints and descriptors)
    and ``copymove.keypoint_stats`` (summary statistics).
    """
    if "image.working" not in ctx.inputs:
        raise NotApplicableError(
            "copymove.sift_keypoints requires image.working; not declared in inputs"
        )

    image = ctx.inputs["image.working"]
    if not isinstance(image, np.ndarray):
        raise InputRejectedError(
            "copymove.sift_keypoints: image.working must be a NumPy array, "
            f"got {type(image).__name__}"
        )

    config = ctx.config if isinstance(ctx.config, Config) else None
    max_keypoints = getattr(config, "max_keypoints", 20000) if config else 20000
    contrast_threshold = getattr(config, "contrast_threshold", 0.04) if config else 0.04
    edge_threshold = getattr(config, "edge_threshold", 10.0) if config else 10.0
    rootsift = getattr(config, "rootsift", True) if config else True
    max_edge = getattr(config, "max_edge", 3000) if config else 3000

    result = analyze_sift_keypoints(
        image,
        max_keypoints=max_keypoints,
        contrast_threshold=contrast_threshold,
        edge_threshold=edge_threshold,
        rootsift=rootsift,
        max_edge=max_edge,
    )

    # Write keypoints as NPZ artifact
    buffer = io.BytesIO()
    np.savez(
        buffer,
        keypoints=result["keypoints"],
        descriptors=result["descriptors"],
    )
    keypoints_artifact_ref = ctx.write_artifact(
        "keypoints.npz", buffer.getvalue(), "application/x-npy"
    )

    # Write descriptors as separate NPZ artifact for large datasets
    buffer = io.BytesIO()
    np.save(buffer, result["descriptors"])
    descriptors_artifact_ref = ctx.write_artifact(
        "descriptors.npy", buffer.getvalue(), "application/x-npy"
    )

    summary = keypoints_result_to_dict(result)
    summary["keypoints_artifact"] = keypoints_artifact_ref
    summary["descriptors_artifact"] = descriptors_artifact_ref

    return {
        "copymove.keypoints": keypoints_artifact_ref,
        "copymove.keypoint_stats": summary,
    }

"""Processor wiring for the SIFT keypoint plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.copymove.sift_keypoints.algorithm` and returns typed
outputs matching ``METADATA.outputs``.
"""

from __future__ import annotations

import io

import numpy as np

from photoproof.contracts.copymove import Keypoints
from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.sift_keypoints.algorithm import (
    analyze_sift_keypoints,
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

    geometry = ctx.inputs.get("image.geometry")
    if not isinstance(geometry, dict) or geometry.get("working_size") != [
        image.shape[1],
        image.shape[0],
    ]:
        raise InputRejectedError(
            "SIFT needs source-bound geometry matching its actual working pixels"
        )
    packed = result["keypoints"]
    features = Keypoints(
        source_sha256=geometry["source_sha256"],
        image_size=(image.shape[1], image.shape[0]),
        xy=packed[:, :2],
        scale=packed[:, 2],
        angle=packed[:, 3],
        response=packed[:, 4],
        desc=result["descriptors"],
    )
    keypoints_artifact_ref = ctx.write_artifact(
        "keypoints.npz", features.to_npz(), "application/x-npz"
    )

    # Write descriptors as separate NPZ artifact for large datasets
    buffer = io.BytesIO()
    np.save(buffer, result["descriptors"], allow_pickle=False)
    descriptors_artifact_ref = ctx.write_artifact(
        "descriptors.npy", buffer.getvalue(), "application/x-npy"
    )

    summary = {
        key: result[key] for key in ("num_keypoints", "density_per_mp", "confidence", "config")
    }
    summary["coordinate_space"] = "working_pixels"
    summary["contract_version"] = features.contract_version
    summary["source_sha256"] = features.source_sha256
    summary["image_size"] = list(features.image_size)
    summary["keypoints_artifact"] = keypoints_artifact_ref
    summary["descriptors_artifact"] = descriptors_artifact_ref

    return {
        "copymove.keypoints": keypoints_artifact_ref,
        "copymove.keypoint_stats": summary,
    }

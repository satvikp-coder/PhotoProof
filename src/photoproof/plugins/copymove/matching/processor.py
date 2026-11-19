"""Processor wiring for the SIFT descriptor matching plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.copymove.matching.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import io

import numpy as np

from photoproof.contracts.arrays import decode_npz_numeric
from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.matching.algorithm import (
    analyze_sift_matching,
)
from photoproof.plugins.copymove.matching.schema import Config


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

    keypoints_data = decode_npz_numeric(
        ctx.artifact("copymove.keypoints"), names=frozenset({"keypoints", "descriptors"})
    )

    keypoints = np.array(keypoints_data["keypoints"], dtype=np.float32)
    descriptors = np.array(keypoints_data["descriptors"], dtype=np.float32)

    if not np.isfinite(keypoints).all() or not np.isfinite(descriptors).all():
        raise InputRejectedError("copymove.matching: keypoint/descriptor values must be finite")
    config = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )

    result = analyze_sift_matching(
        keypoints,
        descriptors,
        k=config.k,
        ratio=config.ratio,
        min_spatial_distance=config.min_spatial_distance,
        max_pairs=config.max_pairs,
    )

    # Write matches as NPZ artifact
    buffer = io.BytesIO()
    np.savez(
        buffer,
        pairs=result["pairs"],
        distances=result["distances"],
    )
    matches_artifact_ref = ctx.write_artifact("matches.npz", buffer.getvalue(), "application/x-npz")

    summary = {
        key: result[key] for key in ("num_keypoints", "num_matches", "matcher_used", "config")
    }
    summary["matches_artifact"] = matches_artifact_ref

    return {
        "copymove.matches": matches_artifact_ref,
        "copymove.match_stats": summary,
    }

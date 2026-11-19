"""Processor wiring for the SIFT descriptor matching plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.copymove.matching.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import numpy as np

from photoproof.contracts.copymove import Keypoints, Matches
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

    geometry = ctx.inputs.get("image.geometry")
    if not isinstance(geometry, dict) or not isinstance(geometry.get("source_sha256"), str):
        raise InputRejectedError("matching requires authoritative source-bound image geometry")
    features = Keypoints.from_npz(
        ctx.artifact("copymove.keypoints"), source_sha256=geometry["source_sha256"]
    )
    if list(features.image_size) != geometry.get("working_size"):
        raise InputRejectedError(
            "matching keypoints disagree with current working image dimensions"
        )
    keypoints = np.column_stack((features.xy, features.scale, features.angle, features.response))
    descriptors = features.desc

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
    matches = Matches(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        num_keypoints=len(features.xy),
        pairs=result["pairs"],
        distance=result["distances"],
    )
    matches_artifact_ref = ctx.write_artifact("matches.npz", matches.to_npz(), "application/x-npz")

    summary = {
        key: result[key] for key in ("num_keypoints", "num_matches", "matcher_used", "config")
    }
    summary["matches_artifact"] = matches_artifact_ref
    summary["contract_version"] = matches.contract_version
    summary["source_sha256"] = matches.source_sha256
    summary["image_size"] = list(matches.image_size)
    summary["coordinate_space"] = matches.coordinate_space

    return {
        "copymove.matches": matches_artifact_ref,
        "copymove.match_stats": summary,
    }

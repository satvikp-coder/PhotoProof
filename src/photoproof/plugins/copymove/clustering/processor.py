"""Processor wiring for the clustering plugin.

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
    analyze_clustering,
    clustering_result_to_dict,
)
from photoproof.plugins.copymove.clustering.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run copy-move clustering on keypoint matches.

    Reads ``copymove.matches`` (NPZ artifact with pairs and distances) and
    ``copymove.keypoints`` (NPZ artifact with keypoints), and returns
    ``copymove.clusters`` (clustering result) and ``copymove.match_stats``
    (summary statistics).
    """
    if "copymove.matches" not in ctx.inputs:
        raise NotApplicableError(
            "copymove.clustering requires copymove.matches; not declared in inputs"
        )
    if "copymove.keypoints" not in ctx.inputs:
        raise NotApplicableError(
            "copymove.clustering requires copymove.keypoints; not declared in inputs"
        )

    matches_data = ctx.inputs["copymove.matches"]
    keypoints_data = ctx.inputs["copymove.keypoints"]

    if not isinstance(matches_data, dict):
        raise InputRejectedError(
            "copymove.clustering: copymove.matches must be a dict, "
            f"got {type(matches_data).__name__}"
        )
    if not isinstance(keypoints_data, dict):
        raise InputRejectedError(
            "copymove.clustering: copymove.keypoints must be a dict, "
            f"got {type(keypoints_data).__name__}"
        )

    if "pairs" not in matches_data or "distances" not in matches_data:
        raise InputRejectedError(
            "copymove.clustering: copymove.matches must contain 'pairs' and 'distances' keys"
        )

    pairs = np.array(matches_data["pairs"], dtype=np.int32)
    _distances = np.array(matches_data["distances"], dtype=np.float32)
    keypoints = np.array(keypoints_data["keypoints"], dtype=np.float32)

    config = ctx.config if isinstance(ctx.config, Config) else None
    method = getattr(config, "method", "hierarchical") if config else "hierarchical"
    cut_threshold = getattr(config, "cut_threshold", 0.5) if config else 0.5
    eps = getattr(config, "eps", 0.5) if config else 0.5
    min_matches_per_cluster = getattr(config, "min_matches_per_cluster", 4) if config else 4

    result = analyze_clustering(
        keypoints,
        pairs,
        method=method,
        cut_threshold=cut_threshold,
        eps=eps,
        min_matches_per_cluster=min_matches_per_cluster,
    )

    # Write clusters as JSON artifact
    clusters_json = json.dumps(result["clusters"], indent=2)
    clusters_artifact_ref = ctx.write_artifact(
        "clusters.json", clusters_json.encode("utf-8"), "application/json"
    )

    summary = clustering_result_to_dict(result)
    summary["clusters_artifact"] = clusters_artifact_ref

    return {
        "copymove.clusters": clusters_artifact_ref,
        "copymove.match_stats": summary,
    }

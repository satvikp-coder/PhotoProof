"""Consume actual source-bound artifacts and publish candidate cluster hypotheses."""

from photoproof.contracts.copymove import Keypoints, Matches
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.copymove.clustering.hypotheses import cluster_matches
from photoproof.plugins.copymove.clustering.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Publish typed cluster JSON; no RANSAC, mask or forgery verdict is implied."""
    geometry = ctx.inputs.get("image.geometry")
    if not isinstance(geometry, dict) or not isinstance(geometry.get("source_sha256"), str):
        raise InputRejectedError("clustering requires authoritative source-bound image geometry")
    source = geometry["source_sha256"]
    features = Keypoints.from_npz(ctx.artifact("copymove.keypoints"), source_sha256=source)
    matches = Matches.from_npz(ctx.artifact("copymove.matches"), source_sha256=source)
    if list(features.image_size) != geometry.get("working_size"):
        raise InputRejectedError("clustering keypoints differ from the current working frame")
    config = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )
    clusters, stats = cluster_matches(features, matches, **config.model_dump())
    artifact = ctx.write_artifact(
        "clusters.json", clusters.to_json().encode("utf-8"), "application/json"
    )
    stats.update(
        {
            "clusters_artifact": artifact,
            "source_sha256": source,
            "image_size": list(features.image_size),
            "coordinate_space": clusters.coordinate_space,
            "contract_version": clusters.contract_version,
            "config": config.model_dump(),
            "clusters": [{"id": c.id, "size": len(c.pair_indices)} for c in clusters.clusters],
        }
    )
    return {"copymove.clusters": artifact, "copymove.cluster_stats": stats}

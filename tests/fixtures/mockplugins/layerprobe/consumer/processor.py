import numpy as np

from photoproof.contracts.evidence_io import read_evidence_layer
from photoproof.contracts.grids import project_scores
from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    geometry = ctx.inputs["image.geometry"]
    evidence = read_evidence_layer(
        ctx, "evidence.layers", ordinal=0, source_sha256=geometry["source_sha256"]
    )
    scores, valid = project_scores(
        evidence.layer.map,
        evidence.layer.valid_mask,
        grid=evidence.sampling_grid,
        geometry=geometry,
        output_shape=tuple(geometry["working_size"][::-1]),
    )
    return {
        "evidence.observation": {
            "shape": list(scores.shape),
            "mean": float(np.mean(scores)),
            "valid_count": int(valid.sum()),
            "source_sha256": evidence.source_sha256,
            "calibration": evidence.layer.calibration,
            "read_only": not evidence.layer.map.flags.writeable
            and not evidence.layer.valid_mask.flags.writeable,
        }
    }

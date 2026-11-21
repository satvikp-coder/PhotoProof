from typing import cast

import numpy as np

from photoproof.contracts.exceptions import NotApplicableError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.similarity import read_similarity


def run(ctx: PluginContext) -> dict[str, object]:
    result = read_similarity(
        ctx,
        target_geometry=cast(ImageGeometry, ctx.inputs["image.geometry"]),
        reference_geometry=cast(ImageGeometry, ctx.inputs["image.reference_geometry"]),
    )
    if result is None:
        raise NotApplicableError("no measured similarity maps")
    return {
        "comparison.similarity_probe": {
            "shape": list(result.dissimilarity.shape),
            "valid_cells": int(result.valid_mask.sum()),
            "maximum_position": list(
                map(
                    int, np.unravel_index(result.dissimilarity.argmax(), result.dissimilarity.shape)
                )
            ),
            "read_only": all(
                not a.flags.writeable
                for a in (
                    result.dissimilarity,
                    result.abs_difference,
                    result.valid_mask,
                    result.edge_suppressed,
                )
            ),
        }
    }

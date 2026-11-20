from typing import cast

from photoproof.contracts.exceptions import NotApplicableError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.registration import read_registration


def run(ctx: PluginContext) -> dict[str, object]:
    registered = read_registration(
        ctx,
        target_geometry=cast(ImageGeometry, ctx.inputs["image.geometry"]),
        reference_geometry=cast(ImageGeometry, ctx.inputs["image.reference_geometry"]),
    )
    if registered is None:
        raise NotApplicableError("registration did not establish usable geometric overlap")
    return {
        "comparison.registration_probe": {
            "shape": list(registered.aligned_reference.shape),
            "overlap_cells": int(registered.overlap_mask.sum()),
            "read_only": all(
                not value.flags.writeable
                for value in (
                    registered.matrix,
                    registered.aligned_reference,
                    registered.overlap_mask,
                    registered.inlier_points,
                )
            ),
            "model": registered.model,
        }
    }

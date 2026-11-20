"""Controller adapter: preserve both source identities and actual geometric artifacts."""

from typing import cast

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.registration import write_registration
from photoproof.plugins.comparison.registration.algorithm import register_pair
from photoproof.plugins.comparison.registration.schema import Config


def _geometry(ctx: PluginContext, *, role: str, pixels: np.ndarray) -> ImageGeometry:
    descriptor = ctx.inputs.get(f"input.{role}")
    name = "image.geometry" if role == "target" else "image.reference_geometry"
    geometry = ctx.inputs.get(name)
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("role") != role
        or not isinstance(geometry, dict)
        or descriptor.get("sha256") != geometry.get("source_sha256")
        or list(pixels.shape[1::-1]) != geometry.get("working_size")
    ):
        raise InputRejectedError(
            f"registration {role} pixels differ from their verified source frame"
        )
    return cast(ImageGeometry, geometry)


def run(ctx: PluginContext) -> dict[str, object]:
    """Return registered artifacts or explicit registration_failed without fabricated pixels."""
    config = (
        ctx.config if isinstance(ctx.config, Config) else Config.model_validate(ctx.config or {})
    )
    target = ctx.inputs.get("image.working")
    reference = ctx.inputs.get("image.reference_working")
    if not isinstance(target, np.ndarray) or not isinstance(reference, np.ndarray):
        raise InputRejectedError("registration requires both actual working-pixel arrays")
    target_geometry = _geometry(ctx, role="target", pixels=target)
    reference_geometry = _geometry(ctx, role="reference", pixels=reference)
    result = register_pair(reference=reference, target=target, config=config, seed=ctx.seed)
    registered = result.status == "registered"
    return write_registration(
        ctx,
        target_geometry=target_geometry,
        reference_geometry=reference_geometry,
        status=result.status,
        requested_model=config.model,
        model=result.model if registered else None,
        matrix=result.matrix,
        aligned_reference=result.aligned_reference,
        overlap_mask=result.overlap_mask,
        inlier_points=result.inlier_points if registered else None,
        quality=result.quality,
        parameters=config.model_dump(),
        flags=result.flags,
    )

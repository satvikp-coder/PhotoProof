"""Consume actual verified registration pixels; never compare unaligned inputs."""

from typing import cast

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.registration import read_registration
from photoproof.contracts.similarity import write_similarity
from photoproof.plugins.comparison.ssim.algorithm import compare_aligned
from photoproof.plugins.comparison.ssim.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    target = ctx.inputs.get("image.working")
    target_geometry = ctx.inputs.get("image.geometry")
    reference_geometry = ctx.inputs.get("image.reference_geometry")
    if (
        not isinstance(target, np.ndarray)
        or not isinstance(target_geometry, dict)
        or not isinstance(reference_geometry, dict)
    ):
        raise InputRejectedError("SSIM requires actual target pixels and both source geometries")
    for role, geometry in (("target", target_geometry), ("reference", reference_geometry)):
        descriptor = ctx.inputs.get(f"input.{role}")
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("role") != role
            or descriptor.get("sha256") != geometry.get("source_sha256")
        ):
            raise InputRejectedError(f"SSIM {role} descriptor differs from its source geometry")
    target_frame = cast(ImageGeometry, target_geometry)
    reference_frame = cast(ImageGeometry, reference_geometry)
    registration = read_registration(
        ctx, target_geometry=target_frame, reference_geometry=reference_frame
    )
    if registration is None:
        raise NotApplicableError("SSIM unavailable: no reliable reference registration")
    config = (
        ctx.config if isinstance(ctx.config, Config) else Config.model_validate(ctx.config or {})
    )
    result = compare_aligned(
        registration.aligned_reference, target, registration.overlap_mask, config=config
    )
    return write_similarity(
        ctx,
        target_geometry=target_frame,
        reference_geometry=reference_frame,
        registration=ctx.inputs["comparison.transform"],
        dissimilarity=result.dissimilarity,
        abs_difference=result.abs_difference,
        valid_mask=result.valid_mask,
        edge_suppressed=result.edge_suppressed,
        facts=result.summary,
    )

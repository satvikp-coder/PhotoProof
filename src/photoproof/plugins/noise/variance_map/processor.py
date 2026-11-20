"""Processor wiring for the noise variance map plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.noise.variance_map.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import re
from typing import cast

import numpy as np

from photoproof.contracts.arrays import decode_npy, encode_npy
from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.grids import validate_pixel_transform
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.noise.variance_map.algorithm import (
    analyze_noise_variance,
)
from photoproof.plugins.noise.variance_map.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run noise variance analysis on the decoded image and residual.

    Reads the verified ``noise.residual`` NPY artifact and ``image.decoded``.
    Returns sigma/consistency NPY artifacts and the validity-aware summary.
    """
    if "noise.residual" not in ctx.inputs:
        raise NotApplicableError(
            "noise.variance_map requires noise.residual; not declared in inputs"
        )
    if "image.decoded" not in ctx.inputs:
        raise NotApplicableError(
            "noise.variance_map requires image.decoded; not declared in inputs"
        )

    residual = ctx.inputs["noise.residual"]
    image = ctx.inputs["image.decoded"]

    if not isinstance(residual, np.ndarray):
        residual = decode_npy(ctx.artifact("noise.residual"), expected_ndim=2)
    if not isinstance(image, np.ndarray):
        raise InputRejectedError(
            f"noise.variance_map: image.decoded must be a NumPy array, got {type(image).__name__}"
        )

    geometry_value = ctx.inputs.get("image.geometry")
    if (
        not isinstance(geometry_value, dict)
        or not isinstance(geometry_value.get("source_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", geometry_value["source_sha256"])
    ):
        raise InputRejectedError(
            "noise variance requires authoritative source-bound image geometry"
        )
    geometry = cast(ImageGeometry, geometry_value)
    validate_pixel_transform(geometry)
    if list(image.shape[:2][::-1]) != geometry["decoded_size"]:
        raise InputRejectedError("noise variance decoded pixels differ from the host source frame")

    config = ctx.config if isinstance(ctx.config, Config) else None
    block = getattr(config, "block", 32) if config else 32
    intensity_bins = getattr(config, "intensity_bins", 8) if config else 8
    texture_threshold = getattr(config, "texture_threshold", 15.0) if config else 15.0
    saturation_threshold = getattr(config, "saturation_threshold", 0.98) if config else 0.98
    min_valid_fraction = getattr(config, "min_valid_fraction", 0.30) if config else 0.30

    result = analyze_noise_variance(
        residual,
        image,
        block=block,
        intensity_bins=intensity_bins,
        texture_threshold=texture_threshold,
        saturation_threshold=saturation_threshold,
        min_valid_fraction=min_valid_fraction,
    )

    # Prepare every bounded numerical payload before publishing references.
    sigma_bytes = encode_npy(result["sigma_map"])
    consistency_bytes = encode_npy(result["consistency_map"])
    validity_bytes = encode_npy(result["valid_mask"])
    sigma_ref = ctx.write_artifact("noise-sigma.npy", sigma_bytes, "application/x-npy")
    consistency_ref = ctx.write_artifact(
        "noise-consistency.npy", consistency_bytes, "application/x-npy"
    )
    validity_ref = ctx.write_artifact("noise-validity.npy", validity_bytes, "application/x-npy")
    common = {"block": result["block"], "stride": result["block"], "notes": []}
    summary = {
        "schema": "photoproof.noise.variance",
        "format_version": 2,
        "source_sha256": geometry["source_sha256"],
        "image_size": geometry["decoded_size"],
        "coordinate_space": "decoded_pixels",
        "grid_shape": list(result["valid_mask"].shape),
        "sigma_map": {**common, "scale": "sigma", "artifact": sigma_ref},
        "consistency_map": {**common, "scale": "robust_z", "artifact": consistency_ref},
        "valid_mask_artifact": validity_ref,
        "valid_fraction": result["valid_fraction"],
        "confidence": result["confidence"],
        "block": result["block"],
        "intensity_bins": result["intensity_bins"],
        "texture_threshold": result["texture_threshold"],
    }
    return {
        "noise.sigma_map": sigma_ref,
        "noise.consistency_map": consistency_ref,
        "noise.variance_summary": summary,
    }

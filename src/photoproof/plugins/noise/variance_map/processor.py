"""Processor wiring for the noise variance map plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.noise.variance_map.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import io

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.noise.variance_map.algorithm import (
    analyze_noise_variance,
    variance_result_to_dict,
)
from photoproof.plugins.noise.variance_map.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run noise variance analysis on the decoded image and residual.

    Reads ``noise.residual`` (float32 array) and ``image.decoded`` (luminance
    image), and returns ``compression.ela_map`` and ``compression.ela_anomaly``.
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
        raise InputRejectedError(
            "noise.variance_map: noise.residual must be a NumPy array, "
            f"got {type(residual).__name__}"
        )
    if not isinstance(image, np.ndarray):
        raise InputRejectedError(
            f"noise.variance_map: image.decoded must be a NumPy array, got {type(image).__name__}"
        )

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

    # Write sigma map as NPZ artifact
    buffer = io.BytesIO()
    np.save(buffer, result["sigma_map"])
    sigma_artifact_ref = ctx.write_artifact(
        "noise_sigma_map.npy", buffer.getvalue(), "application/x-npy"
    )

    # Write consistency map as NPZ artifact
    buffer = io.BytesIO()
    np.save(buffer, result["consistency_map"])
    consistency_artifact_ref = ctx.write_artifact(
        "noise_consistency_map.npy", buffer.getvalue(), "application/x-npy"
    )

    summary = variance_result_to_dict(result)
    summary["sigma_map_artifact"] = sigma_artifact_ref
    summary["consistency_map_artifact"] = consistency_artifact_ref

    return {
        "noise.sigma_map": sigma_artifact_ref,
        "noise.consistency_map": consistency_artifact_ref,
        "noise.variance_summary": summary,
    }

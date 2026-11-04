"""Processor wiring for the noise residual plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.noise.residual.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import io

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.noise.residual.algorithm import analyze_residual
from photoproof.plugins.noise.residual.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run noise residual analysis on the decoded image.

    Reads ``image.decoded`` (luminance channel) and returns
    ``noise.residual`` (float32 NPZ artifact) and ``noise.sigma_global``
    (global noise sigma estimate).
    """
    if "image.decoded" not in ctx.inputs:
        raise NotApplicableError("noise.residual requires image.decoded; not declared in inputs")

    image_array = ctx.inputs["image.decoded"]
    if not isinstance(image_array, np.ndarray):
        raise InputRejectedError(
            f"noise.residual: image.decoded must be a NumPy array, got {type(image_array).__name__}"
        )

    config = ctx.config if isinstance(ctx.config, Config) else None
    wavelet = getattr(config, "wavelet", "db8") if config else "db8"
    levels = getattr(config, "levels", 4) if config else 4
    method = getattr(config, "method", "bayesshrink") if config else "bayesshrink"
    channel = getattr(config, "channel", "luma") if config else "luma"

    result = analyze_residual(
        image_array,
        wavelet=wavelet,
        levels=levels,
        method=method,
        channel=channel,
    )

    # Write residual as float32 NPZ artifact
    buffer = io.BytesIO()
    np.save(buffer, result["residual"])
    artifact_ref = ctx.write_artifact("noise_residual.npy", buffer.getvalue(), "application/x-npy")

    return {
        "noise.residual": artifact_ref,
        "noise.sigma_global": result["sigma_global"],
        "noise.summary": {
            "sigma_global": result["sigma_global"],
            "wavelet": result["wavelet"],
            "levels": result["levels"],
            "method": result["method"],
            "channel": result["channel"],
            "context": result["context"],
        },
    }

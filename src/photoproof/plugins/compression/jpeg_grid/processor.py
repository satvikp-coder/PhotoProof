"""Processor wiring for the JPEG grid plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.compression.jpeg_grid.algorithm` and returns typed
outputs matching ``METADATA.outputs``.
"""

from __future__ import annotations

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.compression.jpeg_grid.algorithm import (
    analyze_jpeg_grid,
    grid_result_to_dict,
)
from photoproof.plugins.compression.jpeg_grid.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run JPEG grid analysis on the decoded image.

    Reads ``image.decoded`` (luminance channel) and returns
    ``compression.grid_origin`` and ``compression.grid_map``.
    """
    if "image.decoded" not in ctx.inputs:
        raise NotApplicableError(
            "compression.jpeg_grid requires image.decoded; not declared in inputs"
        )

    image_array = ctx.inputs["image.decoded"]
    if not isinstance(image_array, np.ndarray):
        raise InputRejectedError(
            "compression.jpeg_grid: image.decoded must be a NumPy array, "
            f"got {type(image_array).__name__}"
        )

    config = ctx.config if isinstance(ctx.config, Config) else None
    window = getattr(config, "window", 64) if config else 64
    stride = getattr(config, "stride", 32) if config else 32

    result = analyze_jpeg_grid(image_array, window=window, stride=stride)
    summary = grid_result_to_dict(result)

    return {
        "compression.grid_origin": summary,
        "compression.grid_map": summary["anomaly"],
    }

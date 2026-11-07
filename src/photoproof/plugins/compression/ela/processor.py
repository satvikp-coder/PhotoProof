"""Processor wiring for the ELA plugin.

Calls the pure algorithm in
:mod:`photoproof.plugins.compression.ela.algorithm` and returns typed outputs
matching ``METADATA.outputs``.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image as PILImage

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.compression.ela.algorithm import analyze_ela, ela_result_to_dict
from photoproof.plugins.compression.ela.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run ELA analysis on the decoded image.

    Reads ``image.decoded`` (unconverted colour pixels) and returns
    ``compression.ela_map`` (artifact) and ``compression.ela_anomaly``
    (AnomalyMap dict).
    """
    if "image.decoded" not in ctx.inputs:
        raise NotApplicableError("compression.ela requires image.decoded; not declared in inputs")

    image_array = ctx.inputs["image.decoded"]
    if not isinstance(image_array, np.ndarray):
        raise InputRejectedError(
            "compression.ela: image.decoded must be a NumPy array, "
            f"got {type(image_array).__name__}"
        )

    config = ctx.config if isinstance(ctx.config, Config) else None
    quality = getattr(config, "quality", 95) if config else 95
    aggregate = getattr(config, "aggregate", "max") if config else "max"
    block = getattr(config, "block", 16) if config else 16
    edge_suppression = getattr(config, "edge_suppression", True) if config else True

    result = analyze_ela(
        image_array,
        quality=quality,
        aggregate=aggregate,
        block=block,
        edge_suppression=edge_suppression,
    )

    # Write the ELA map as a PNG artifact
    pil_ela = PILImage.fromarray(result.ela_map, mode="L")
    buffer = io.BytesIO()
    pil_ela.save(buffer, format="PNG")
    artifact_ref = ctx.write_artifact("ela_map.png", buffer.getvalue(), "image/png")

    summary = ela_result_to_dict(result)
    summary["artifact"] = artifact_ref

    return {
        "compression.ela_map": artifact_ref,
        "compression.ela_anomaly": summary["anomaly"],
        "compression.ela_summary": summary,
    }

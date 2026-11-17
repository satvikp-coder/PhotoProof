"""Persist segmentation without modifying upstream evidence."""

from __future__ import annotations

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.regions.algorithm import segment
from photoproof.plugins.fusion.regions.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Write a PNG artifact and regions in the fused grid coordinate space."""
    config = ctx.config if isinstance(ctx.config, Config) else Config()
    value = ctx.inputs.get("fusion.map")
    if value is None or (isinstance(value, list) and not value):
        return {
            "fusion.mask": None,
            "fusion.regions": [],
            "fusion.segmentation": {"status": "unavailable", "truncated": False},
        }
    try:
        fused = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise InputRejectedError(
            "fusion.regions: invalid fusion.map; supply a 2-D score grid"
        ) from error
    mask, regions, summary = segment(
        fused, evidence=ctx.inputs.get("fusion.evidence", []), config=config
    )
    success, encoded = cv2.imencode(".png", mask * 255)
    if not success:
        raise InputRejectedError("fusion.regions: PNG encoding failed; inspect the fused grid")
    return {
        "fusion.mask": ctx.write_artifact("mask.png", encoded.tobytes(), "image/png"),
        "fusion.regions": regions,
        "fusion.segmentation": summary,
    }

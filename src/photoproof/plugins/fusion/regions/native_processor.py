"""Read binary measured fusion and persist validity-safe localized regions."""

from hashlib import sha256
from typing import cast

import cv2

from photoproof.contracts.contributions import read_contributions
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import read_fused_scores
from photoproof.contracts.grids import validate_pixel_transform
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.regions.native import segment_native
from photoproof.plugins.fusion.regions.schema import Config


def run_native(ctx: PluginContext) -> dict[str, object]:
    geometry_value = ctx.inputs.get("image.geometry")
    if ctx.inputs.get("fusion.map") is None:
        return {
            "fusion.mask": None,
            "fusion.regions": [],
            "fusion.segmentation": {"status": "unavailable", "truncated": False},
        }
    if not isinstance(geometry_value, dict):
        raise InputRejectedError("segmentation cannot align evidence without source-bound geometry")
    geometry = cast(ImageGeometry, geometry_value)
    validate_pixel_transform(geometry)
    fused = read_fused_scores(ctx, geometry=geometry)
    if fused is None:
        raise InputRejectedError("segmentation expected a measured fused map")
    entries = read_contributions(ctx, geometry=geometry, fused=fused)
    cfg = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )
    mask, regions, summary = segment_native(fused, entries, config=cfg)
    success, encoded = cv2.imencode(".png", mask * 255)
    if not success:
        raise InputRejectedError("segmentation PNG encoding failed; no localized output produced")
    data = encoded.tobytes()
    return {
        "fusion.mask": ctx.write_artifact("mask.png", data, "image/png"),
        "fusion.regions": regions,
        "fusion.segmentation": {
            **summary,
            "mask_sha256": sha256(data).hexdigest(),
        },
    }

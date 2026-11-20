"""Persist only derived pixels through the framework artifact writer."""

import re
from hashlib import sha256
from typing import cast

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.grids import validate_pixel_transform
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.support.preview.algorithm import encode_preview
from photoproof.plugins.support.preview.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Produce a display-only preview; original evidence is never read or replaced."""
    config = ctx.config if isinstance(ctx.config, Config) else Config()
    raw = ctx.inputs.get("image.geometry")
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("source_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", raw["source_sha256"])
    ):
        raise InputRejectedError("preview requires source-bound working image geometry")
    geometry = cast(ImageGeometry, raw)
    validate_pixel_transform(geometry)
    pixels = ctx.image("image.working")
    if list(pixels.shape[:2][::-1]) != geometry["working_size"]:
        raise InputRejectedError("preview pixels differ from host working dimensions")
    data, width, height = encode_preview(pixels, max_edge=config.max_edge)
    return {
        "support.preview": ctx.write_artifact("derived-preview.png", data, "image/png"),
        "support.preview_info": {
            "format_version": 2,
            "source_sha256": geometry["source_sha256"],
            "working_size": geometry["working_size"],
            "coordinate_space": "working_pixels",
            "preview_sha256": sha256(data).hexdigest(),
            "resampling": "lanczos_display_only",
            "kind": "derived_preview",
            "evidence": False,
            "width": width,
            "height": height,
            "alpha": "retained",
            "metadata": "removed",
            "max_edge": config.max_edge,
            "label": "Derived preview - not original evidence",
        },
    }

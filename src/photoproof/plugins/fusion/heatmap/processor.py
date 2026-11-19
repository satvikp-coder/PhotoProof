"""Render only a hash-verified safe preview; never open original evidence."""

from __future__ import annotations

import hashlib
import io
from typing import Any

import cv2
import numpy as np
from PIL import Image

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.heatmap.colormap import CIVIDIS
from photoproof.plugins.fusion.heatmap.schema import Config


def _png(pixels: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()


def render(
    scores: np.ndarray, preview: np.ndarray, *, regions: object, config: Config
) -> tuple[bytes, bytes, bytes]:
    """Render RGBA scores and outlines, plus RGB overlay, at preview resolution."""
    if (
        scores.ndim != 2
        or scores.size == 0
        or not np.isfinite(scores).all()
        or np.any(scores < 0)
        or np.any(scores > 1)
    ):
        raise InputRejectedError("heatmap: expected a finite 2-D score grid in [0, 1]")
    height, width = preview.shape[:2]
    resized = cv2.resize(
        scores.astype(np.float32), (width, height), interpolation=cv2.INTER_NEAREST
    )
    colors = CIVIDIS[np.rint(resized * 255).astype(np.uint8)]
    visible = resized >= config.display_threshold
    alpha = np.where(visible, 255, 0).astype(np.uint8)
    heat = np.dstack((colors, alpha))
    # Context comes exclusively from the generated preview, composited on neutral gray.
    context = Image.fromarray(preview).convert("RGBA")
    background = Image.new("RGBA", context.size, (128, 128, 128, 255))
    background.alpha_composite(context)
    gray = np.array(background.convert("L"), dtype=np.uint8)
    gray_rgb = np.repeat(gray[..., None], 3, axis=2)
    amount = visible[..., None] * config.opacity
    overlay = np.rint(gray_rgb * (1 - amount) + colors * amount).astype(np.uint8)
    outlines = np.zeros((height, width, 4), dtype=np.uint8)
    if isinstance(regions, list):
        for region in regions:
            if not isinstance(region, dict):
                continue
            polygon = region.get("polygon")
            if not isinstance(polygon, list) or len(polygon) < 3:
                continue
            points: np.ndarray = np.asarray(polygon, dtype=np.float64)
            if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
                raise InputRejectedError("heatmap: invalid region polygon; inspect fusion.regions")
            points[:, 0] *= width / scores.shape[1]
            points[:, 1] *= height / scores.shape[0]
            points[:, 0] = np.clip(points[:, 0], 0, width - 1)
            points[:, 1] = np.clip(points[:, 1], 0, height - 1)
            cv2.polylines(
                outlines,
                [np.rint(points).astype(np.int32)],
                True,
                (255, 255, 255, 255),
                1,
                lineType=cv2.LINE_8,
            )
    outlines[..., 3] = np.where(visible, outlines[..., 3], 0)
    overlay[outlines[..., 3] > 0] = 255
    return _png(heat), _png(overlay), _png(outlines)


def run(ctx: PluginContext) -> dict[str, object]:
    """Produce PNG artifacts with an explicit uncalibrated legend and preview hash."""
    config = ctx.config if isinstance(ctx.config, Config) else Config()
    legend: dict[str, Any] = {
        "colormap": "cividis",
        "range": [0, 1],
        "label": "heuristic suspicion score (uncalibrated)",
        "notes": [
            "Uncalibrated heuristic; requires corroboration and expert interpretation.",
            "Thresholds are provisional until calibration (#37).",
            "Derived preview context; transparent pixels are below the display threshold.",
        ],
        "display_threshold": config.display_threshold,
        "opacity": config.opacity,
    }
    value = ctx.inputs.get("fusion.map")
    if value is None or (isinstance(value, list) and not value):
        legend.update(status="unavailable", message="no fused evidence available")
        return {
            "fusion.heatmap": None,
            "fusion.overlay": None,
            "fusion.outlines": None,
            "fusion.legend": legend,
        }
    raw_ref = ctx.inputs.get("support.preview")
    info = ctx.inputs.get("support.preview_info")
    if not isinstance(raw_ref, dict) or not isinstance(info, dict):
        raise InputRejectedError("heatmap: safe preview unavailable; enable support.preview")
    ref = ArtifactRef.model_validate(raw_ref.get("artifact"))
    max_edge = info.get("max_edge")
    if isinstance(max_edge, bool) or not isinstance(max_edge, int) or not 1 <= max_edge <= 4096:
        raise InputRejectedError("heatmap: invalid preview bound; rerun support.preview")
    data = ctx.artifact("support.preview")
    if hashlib.sha256(data).hexdigest() != ref.sha256:
        raise InputRejectedError("heatmap: safe preview hash mismatch; preserve the artifact")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != "PNG"
                or image.mode not in {"L", "RGB", "RGBA"}
                or min(image.size) < 1
                or max(image.size) > max_edge
                or image.size != (info.get("width"), info.get("height"))
            ):
                raise InputRejectedError(
                    "heatmap: preview header disagrees with bounded derived metadata"
                )
            preview = np.array(image)
    except (OSError, ValueError) as error:
        raise InputRejectedError("heatmap: invalid derived PNG; preserve the artifact") from error
    try:
        scores = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise InputRejectedError("heatmap: invalid fused map; inspect fusion.rules") from error
    heat, overlay, outlines = render(
        scores, preview, regions=ctx.inputs.get("fusion.regions"), config=config
    )
    legend.update(status="rendered", preview_sha256=ref.sha256, dimensions=list(preview.shape[:2]))
    return {
        "fusion.heatmap": ctx.write_artifact("heuristic-heatmap.png", heat, "image/png"),
        "fusion.overlay": ctx.write_artifact("derived-overlay.png", overlay, "image/png"),
        "fusion.outlines": ctx.write_artifact("region-outlines.png", outlines, "image/png"),
        "fusion.legend": legend,
    }

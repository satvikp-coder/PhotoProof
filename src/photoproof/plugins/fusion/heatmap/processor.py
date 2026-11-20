"""Render only a hash-verified safe preview; never open original evidence."""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.heatmap.colormap import CIVIDIS
from photoproof.plugins.fusion.heatmap.native_processor import run_native
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
    """Run the source-bound binary production pipeline."""
    return run_native(ctx)

"""Validity-aware display sampling and bounded source-matched derived rendering."""

import cv2
import numpy as np
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import MAX_FUSED_CELLS
from photoproof.plugins.fusion.heatmap.colormap import CIVIDIS
from photoproof.plugins.fusion.heatmap.encoding import png
from photoproof.plugins.fusion.heatmap.schema import Config


def render_native(
    scores: np.ndarray,
    valid: np.ndarray,
    preview: np.ndarray,
    *,
    regions: object,
    config: Config | None = None,
) -> tuple[bytes, bytes, bytes]:
    """Unknown cells remain transparent even at threshold zero; context stays derived."""
    cfg = config or Config()
    if (
        not isinstance(scores, np.ndarray)
        or scores.ndim != 2
        or scores.dtype != np.float32
        or not scores.size
        or scores.size > MAX_FUSED_CELLS
        or not np.isfinite(scores).all()
        or np.any((scores < 0) | (scores > 1))
        or not isinstance(valid, np.ndarray)
        or valid.dtype != np.bool_
        or valid.shape != scores.shape
        or np.any(scores[~valid] != 0)
        or not isinstance(preview, np.ndarray)
        or preview.dtype != np.uint8
        or preview.ndim not in (2, 3)
        or not preview.size
        or (preview.ndim == 3 and preview.shape[2] not in (3, 4))
        or max(preview.shape[:2]) > 4096
    ):
        raise InputRejectedError(
            "heatmap needs finite typed scores, validity and bounded derived pixels"
        )
    height, width = preview.shape[:2]
    xs = np.minimum(
        ((np.arange(width) + 0.5) * scores.shape[1] / width).astype(np.int64), scores.shape[1] - 1
    )
    ys = np.minimum(
        ((np.arange(height) + 0.5) * scores.shape[0] / height).astype(np.int64), scores.shape[0] - 1
    )
    sampled = scores[np.ix_(ys, xs)]
    supported = valid[np.ix_(ys, xs)]
    colors = CIVIDIS[np.rint(sampled * 255).astype(np.uint8)]
    visible = supported & (sampled >= cfg.display_threshold)
    heat = np.dstack((colors, np.where(visible, 255, 0).astype(np.uint8)))
    context = Image.fromarray(preview).convert("RGBA")
    background = Image.new("RGBA", context.size, (128, 128, 128, 255))
    background.alpha_composite(context)
    gray = np.asarray(background.convert("L"), dtype=np.uint8)
    gray_rgb = np.repeat(gray[..., None], 3, axis=2)
    amount = visible[..., None] * cfg.opacity
    overlay = np.rint(gray_rgb * (1 - amount) + colors * amount).astype(np.uint8)
    outlines = np.zeros((height, width, 4), np.uint8)
    if not isinstance(regions, list) or len(regions) > 100:
        raise InputRejectedError("heatmap region metadata must be a bounded list")
    vertices = 0
    for region in regions:
        if not isinstance(region, dict) or not isinstance(region.get("holes", []), list):
            raise InputRejectedError("heatmap has invalid region boundary metadata")
        for ring in [region.get("polygon"), *region.get("holes", [])]:
            if not isinstance(ring, list):
                raise InputRejectedError(
                    "heatmap boundary must contain pixel-center coordinate pairs"
                )
            vertices += len(ring)
            if vertices > 16_384:
                raise InputRejectedError("heatmap boundary vertices exceed the display budget")
            if len(ring) < 3:
                continue
            try:
                points: np.ndarray = np.asarray(ring, dtype=np.float64)
            except (ValueError, TypeError, OverflowError) as error:
                raise InputRejectedError("heatmap boundary coordinates are invalid") from error
            if (
                points.shape != (len(ring), 2)
                or not np.isfinite(points).all()
                or np.any(points < 0)
                or np.any(points[:, 0] >= scores.shape[1])
                or np.any(points[:, 1] >= scores.shape[0])
            ):
                raise InputRejectedError("heatmap boundary is outside its recorded fused grid")
            points = (points + 0.5) * [width / scores.shape[1], height / scores.shape[0]] - 0.5
            points = np.clip(np.rint(points), [0, 0], [width - 1, height - 1]).astype(np.int32)
            cv2.polylines(outlines, [points], True, (255, 255, 255, 255), 1, lineType=cv2.LINE_8)
    outlines[..., 3] = np.where(visible, outlines[..., 3], 0)
    overlay[outlines[..., 3] != 0] = 255
    return png(heat), png(overlay), png(outlines)

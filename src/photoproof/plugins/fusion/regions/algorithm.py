"""Deterministic segmentation on the fused map's own coordinate grid."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.fusion.regions.schema import Config


def hysteresis(scores: np.ndarray, *, high: float, low: float) -> np.ndarray:
    """Keep 8-connected low-threshold components containing a high-threshold seed."""
    _, labels = cv2.connectedComponents((scores >= low).astype(np.uint8), connectivity=8)
    seeded = np.unique(labels[scores >= high])
    seeded = seeded[seeded != 0]
    return np.isin(labels, seeded).astype(np.uint8)


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill enclosed background using an exterior border, including edge cases."""
    padded = np.pad(mask, 1).astype(np.uint8)
    cv2.floodFill(padded, None, (0, 0), 1)
    return (mask | (1 - padded[1:-1, 1:-1])).astype(np.uint8)


def _contributors(evidence: object, component: np.ndarray) -> list[str]:
    """Reference upstream evidence regions spatially intersecting this component."""
    names: set[str] = set()
    if not isinstance(evidence, list):
        return []
    height, width = component.shape
    for entry in evidence:
        if not isinstance(entry, dict) or entry.get("reason") == "unavailable":
            continue
        region = entry.get("region")
        if not isinstance(region, dict):
            continue
        rows, cols = region.get("rows"), region.get("cols")
        if not isinstance(rows, list) or not isinstance(cols, list) or len(rows) != len(cols):
            continue
        intersects = any(
            isinstance(y, int)
            and isinstance(x, int)
            and 0 <= y < height
            and 0 <= x < width
            and component[y, x]
            for y, x in zip(rows, cols, strict=True)
        )
        detectors = entry.get("contributing_detectors", [])
        if intersects and isinstance(detectors, list):
            names.update(name for name in detectors if isinstance(name, str))
    return sorted(names)


def segment(
    scores: np.ndarray, *, evidence: object, config: Config
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, object]]:
    """Return a binary uint8 mask and bounded regions from a finite [0, 1] grid.

    Areas count pixels; boxes use [x0, y0, x1, y1] with exclusive upper bounds.
    The mask retains all area-filtered components when the region list is capped.
    """
    if (
        scores.ndim != 2
        or scores.size == 0
        or not np.isfinite(scores).all()
        or np.any(scores < 0)
        or np.any(scores > 1)
    ):
        raise InputRejectedError(
            "fusion.regions: fusion.map must be a nonempty finite 2-D grid in [0, 1]; "
            "inspect fusion.rules output"
        )
    binary = hysteresis(scores, high=config.high, low=config.low)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (config.kernel_size,) * 2)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    binary = fill_holes(binary)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    minimum = max(1, int(np.ceil(scores.size * config.min_area_fraction)))
    kept = [i for i in range(1, count) if int(stats[i, cv2.CC_STAT_AREA]) >= minimum]
    kept.sort(key=lambda i: (-int(stats[i, cv2.CC_STAT_AREA]), i))
    mask = np.isin(labels, kept).astype(np.uint8)
    regions: list[dict[str, Any]] = []
    for label in kept[: config.max_regions]:
        component = labels == label
        contours, _ = cv2.findContours(
            component.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        contour = max(contours, key=cv2.contourArea)
        polygon = cv2.approxPolyDP(contour, config.polygon_tolerance, True).reshape(-1, 2)
        x, y, width, height, area = (int(v) for v in stats[label])
        if len(polygon) < 3:
            polygon = np.array([[x, y], [x + width, y], [x + width, y + height], [x, y + height]])
        regions.append(
            {
                "id": len(regions) + 1,
                "polygon": polygon.tolist(),
                "bbox": [x, y, x + width, y + height],
                "area": area,
                "max_score": float(scores[component].max()),
                "mean_score": float(scores[component].mean()),
                "contributing_detectors": _contributors(evidence, component),
                "full_image": area == scores.size,
            }
        )
    return (
        mask,
        regions,
        {
            "status": "segmented" if kept else "no_regions",
            "grid_shape": list(scores.shape),
            "coordinate_space": "fusion.map",
            "calibration": "uncalibrated",
            "region_count": len(kept),
            "truncated": len(kept) > config.max_regions,
            "full_image": bool(np.all(mask)),
        },
    )

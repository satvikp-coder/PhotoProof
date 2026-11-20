"""Bounded pixel-center boundary rings; label PNG remains the authoritative raster."""

import cv2
import numpy as np

from photoproof.contracts.copymove import Mask
from photoproof.contracts.exceptions import InputRejectedError


def polygons(mask: Mask) -> list[dict[str, object]]:
    """Return connected component pixel areas and outer/hole contours, never box polygons."""
    result: list[dict[str, object]] = []
    vertices = 0
    rings = 0
    for kind, value in (("source", 1), ("target", 2)):
        binary = (mask.mask == value).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        if len(result) + count - 1 > 1000:
            raise InputRejectedError("mask exceeds one thousand polygon components")
        contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        rings += len(contours)
        vertices += sum(len(contour) for contour in contours)
        if rings > 2000 or vertices > 65_536:
            raise InputRejectedError("mask boundary rings or vertices exceed the metadata budget")
        for i, contour in enumerate(contours):
            if hierarchy[0, i, 3] >= 0:
                continue
            x, y = contour[0, 0]
            component = int(labels[y, x])
            left, top, width, height, area = (int(v) for v in stats[component])
            holes = []
            child = int(hierarchy[0, i, 2])
            while child >= 0:
                holes.append(contours[child][:, 0, :].tolist())
                child = int(hierarchy[0, child, 0])
            result.append(
                {
                    "kind": kind,
                    "bbox": [top, left, top + height, left + width],
                    "area_pixels": area,
                    "outer": contour[:, 0, :].tolist(),
                    "holes": holes,
                }
            )
    return sorted(result, key=lambda r: (str(r["kind"]), str(r["bbox"])))

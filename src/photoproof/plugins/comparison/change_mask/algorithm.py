"""Discrete measured differences with explicit unknown support and bounded metadata."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.plugins.comparison.change_mask.schema import Config


@dataclass(frozen=True)
class ChangeRegions:
    mask: np.ndarray
    regions: tuple[Mapping[str, object], ...]
    summary: Mapping[str, object]
    labels: np.ndarray | None = None


def extract_changes(
    dissimilarity: np.ndarray,
    valid: np.ndarray,
    *,
    config: Config | None = None,
) -> ChangeRegions:
    """Threshold actual D; missing/unmeasured comparisons cannot become no differences."""
    cfg = config or Config()
    if (
        not isinstance(dissimilarity, np.ndarray)
        or dissimilarity.ndim != 2
        or dissimilarity.dtype not in (np.float32, np.float64)
        or not dissimilarity.size
        or dissimilarity.size > cfg.max_pixels
        or not isinstance(valid, np.ndarray)
        or valid.dtype != np.bool_
        or valid.shape != dissimilarity.shape
    ):
        raise InputRejectedError(
            "change extraction requires bounded 2D float scores and matching boolean validity"
        )
    if (
        not np.isfinite(dissimilarity).all()
        or np.any((dissimilarity < 0) | (dissimilarity > 1))
        or dissimilarity[~valid].any()
    ):
        raise InputRejectedError(
            "change dissimilarity must be finite unit-interval data with zero invalid storage fill"
        )
    if not valid.any():
        raise NotApplicableError("change extraction has no measured comparison support")
    if cfg.threshold == "otsu":
        samples = np.rint(dissimilarity[valid] * 255).astype(np.uint8).reshape(-1, 1)
        selected, _ = cv2.threshold(samples, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        automatic = float(selected / 255)
        threshold = max(cfg.min_threshold, automatic)
    else:
        automatic = None
        threshold = float(cfg.threshold)
    binary = ((dissimilarity > threshold) & valid).astype(np.uint8)
    initial_pixels = int(binary.sum())
    if cfg.kernel_size > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.kernel_size, cfg.kernel_size))
        binary = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0
        )
        binary = (binary.astype(bool) & valid).astype(np.uint8)
        binary = cv2.morphologyEx(
            binary, cv2.MORPH_CLOSE, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0
        )
        binary = (binary.astype(bool) & valid).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStatsWithAlgorithm(
        binary, 8, cv2.CV_32S, cv2.CCL_WU
    )
    minimum = max(1, math.ceil(cfg.min_region_area * dissimilarity.size))
    ids = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= minimum)
    ids = ids[ids != 0]
    # A lookup avoids an O(component_count * image_pixels) loop. Raster retains all
    # qualifying components even when the bounded table is explicitly truncated.
    admitted = np.zeros(count, bool)
    admitted[ids] = True
    mask = admitted[labels]
    sums = np.bincount(labels.ravel(), weights=dissimilarity.ravel(), minlength=count)
    ordered = sorted(
        ids.tolist(),
        key=lambda i: (
            -int(stats[i, cv2.CC_STAT_AREA]),
            int(stats[i, cv2.CC_STAT_TOP]),
            int(stats[i, cv2.CC_STAT_LEFT]),
        ),
    )
    regions = []
    for i in ordered[: cfg.max_regions]:
        x, y, width, height, area = map(int, stats[i])
        regions.append(
            MappingProxyType(
                {
                    "id": i,
                    "bbox": [x, y, width, height],
                    "area": area,
                    "area_fraction": area / dissimilarity.size,
                    "mean_dissimilarity": float(sums[i] / area),
                    "category": "difference",
                }
            )
        )
    changed = int(mask.sum())
    summary: dict[str, object] = {
        "status": "differences_detected" if changed else "no_differences_detected",
        "category": "difference",
        "threshold_method": "otsu" if automatic is not None else "fixed",
        "threshold": threshold,
        "min_threshold": cfg.min_threshold,
        "automatic_threshold": automatic,
        "otsu_quantization": "nearest_uint8_divided_by_255" if automatic is not None else None,
        "min_region_area_fraction": cfg.min_region_area,
        "min_region_area_pixels": minimum,
        "kernel_size": cfg.kernel_size,
        "cleanup": "elliptical_open_then_close_clipped_to_validity",
        "initial_threshold_pixels": initial_pixels,
        "changed_pixels": changed,
        "measured_pixels": int(valid.sum()),
        "target_pixels": dissimilarity.size,
        "region_count": len(ids),
        "reported_region_count": len(regions),
        "regions_truncated": len(ids) > cfg.max_regions,
        "removed_small_components": count - 1 - len(ids),
        "low_specificity": changed / int(valid.sum()) >= 0.8,
        "interpretation": "measured differences between supplied inputs at configured sensitivity",
        "limitations": [
            "differences_do_not_establish_manipulation",
            "registration_error",
            "compression_and_threshold_sensitivity",
        ],
    }
    mask.setflags(write=False)
    retained_labels = np.where(mask, labels, 0).astype(np.int32)
    retained_labels.setflags(write=False)
    return ChangeRegions(mask, tuple(regions), MappingProxyType(summary), retained_labels)

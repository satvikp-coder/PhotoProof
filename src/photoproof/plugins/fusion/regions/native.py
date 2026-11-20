"""Validity-clipped cleanup with bounded boundaries and original-component attribution."""

import cv2
import numpy as np

from photoproof.contracts.contributions import Contribution
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import FusedScores, validate_fused_scores
from photoproof.plugins.fusion.regions.algorithm import fill_holes, hysteresis
from photoproof.plugins.fusion.regions.schema import Config


def segment_native(
    fused: FusedScores,
    contributions: tuple[Contribution, ...],
    *,
    config: Config | None = None,
) -> tuple[np.ndarray, list[dict[str, object]], dict[str, object]]:
    """Clean measured cells only; never infer support inside an unmeasured hole."""
    cfg = config or Config()
    validate_fused_scores(fused)
    cv2.setNumThreads(1)
    initial = hysteresis(np.where(fused.valid_mask, fused.scores, 0), high=cfg.high, low=cfg.low)
    count_source, source_labels = cv2.connectedComponents(initial, connectivity=8)
    component_evidence: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    detectors: dict[str, np.ndarray] = {}
    contexts: dict[str, np.ndarray] = {}
    for entry in contributions:
        if entry.scores.shape != initial.shape:
            raise InputRejectedError("segmentation contribution grid differs from fused evidence")
        present = np.zeros(count_source, bool)
        present[np.unique(source_labels[entry.scores > 0])] = True
        present[0] = False
        positive = (entry.scores > 0) & (source_labels != 0)
        component_ids = source_labels[positive]
        values = entry.scores[positive]
        counts = np.bincount(component_ids, minlength=count_source).astype(np.uint32)
        sums = np.bincount(component_ids, weights=values, minlength=count_source)
        maxima = np.zeros(count_source, np.float32)
        np.maximum.at(maxima, component_ids, values)
        component_evidence[entry.detector_id] = (counts, sums, maxima)
        (contexts if entry.scope == "global_context" else detectors)[entry.detector_id] = present
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.kernel_size,) * 2)
    binary = cv2.morphologyEx(initial, cv2.MORPH_OPEN, kernel) & fused.valid_mask.astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel) & fused.valid_mask.astype(np.uint8)
    binary = fill_holes(binary) & fused.valid_mask.astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        np.asarray(binary, dtype=np.uint8), connectivity=8
    )
    minimum = max(1, int(np.ceil(fused.scores.size * cfg.min_area_fraction)))
    candidates = [i for i in range(1, count) if int(stats[i, cv2.CC_STAT_AREA]) >= minimum]
    candidates.sort(key=lambda i: (-int(stats[i, cv2.CC_STAT_AREA]), i))
    kept = []
    regions: list[dict[str, object]] = []
    vertices = 0
    omitted = 0
    for label in candidates:
        x, y, width, height, area = (int(v) for v in stats[label])
        local = labels[y : y + height, x : x + width] == label
        origins = np.unique(source_labels[y : y + height, x : x + width][local])
        origins = origins[origins != 0]
        if not len(origins):
            continue  # A detached cleanup halo has no original evidence support.
        names = sorted(name for name, present in detectors.items() if present[origins].any())
        if not names:
            raise InputRejectedError(
                "retained segmentation region has no spatial contribution references"
            )
        kept.append(label)
        if len(regions) >= cfg.max_regions:
            omitted += 1
            continue
        contours, hierarchy = cv2.findContours(
            local.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )
        if hierarchy is None:
            raise InputRejectedError("segmentation boundary extraction produced no component")
        outer = [i for i in range(len(contours)) if hierarchy[0, i, 3] < 0]
        if len(outer) != 1:
            raise InputRejectedError("segmentation component has ambiguous outer boundaries")
        rings = [cv2.approxPolyDP(c, cfg.polygon_tolerance, True).reshape(-1, 2) for c in contours]
        size = sum(len(ring) for ring in rings)
        if size > 4096 or vertices + size > 16_384:
            omitted += 1
            continue  # Raster retains the region; bounded metadata explicitly reports omission.
        vertices += size
        index = outer[0]
        offset = np.array([x, y])
        polygon = (rings[index] + offset).tolist()
        holes = [
            (ring + offset).tolist() for i, ring in enumerate(rings) if hierarchy[0, i, 3] >= 0
        ]
        values = fused.scores[y : y + height, x : x + width][local]
        explanations: list[dict[str, object]] = []
        region_rules: set[str] = set()
        region_families: set[str] = set()
        for entry in contributions:
            counts, sums, maxima = component_evidence[entry.detector_id]
            cells = int(counts[origins].sum())
            if not cells:
                continue
            gate = {
                "strong": "strong_maximum",
                "medium": "medium_high_or_independent_family_support",
                "weak": "weak_boost_existing_nonweak_support",
            }[entry.strength_class]
            rules = [gate]
            if entry.strength_class != "strong":
                rules.append("independent_family_maximum")
            region_rules.update(rules)
            region_families.add(entry.family)
            explanations.append(
                {
                    "detector_id": entry.detector_id,
                    "family": entry.family,
                    "strength_class": entry.strength_class,
                    "effective_weight": entry.effective_weight,
                    "scope": entry.scope,
                    "measurement_scope": "original_hysteresis_components",
                    "accepted_cell_count": cells,
                    "max_accepted_score": float(maxima[origins].max()),
                    "mean_accepted_score": float(sums[origins].sum() / cells),
                    "rules_fired": rules,
                }
            )
        regions.append(
            {
                "id": len(regions) + 1,
                "polygon": polygon,
                "holes": holes,
                "boundary_kind": "pixel_centers"
                if len(polygon) >= 3
                else "degenerate_pixel_centers",
                "bbox": [x, y, x + width, y + height],
                "area": area,
                "max_score": float(values.max()),
                "mean_score": float(values.mean()),
                "contributing_detectors": names,
                "contributions": explanations,
                "families": sorted(region_families),
                "rules_fired": sorted(region_rules),
                "global_context_detectors": sorted(
                    name for name, present in contexts.items() if present[origins].any()
                ),
                "source_component_count": len(origins),
                "full_image": area == fused.scores.size,
            }
        )
    mask = np.isin(labels, kept).astype(np.uint8)
    return (
        mask,
        regions,
        {
            "status": "segmented" if kept else "no_regions",
            "source_sha256": fused.source_sha256,
            "working_size": list(fused.working_size),
            "grid_shape": list(fused.scores.shape),
            "coordinate_space": "uniform_working_image_cells",
            "calibration": "uncalibrated",
            "boundary_convention": "pixel_centers_xy",
            "region_count": len(kept),
            "metadata_region_count": len(regions),
            "truncated": omitted > 0,
            "omitted_region_count": omitted,
            "full_image": bool(mask.all()),
            "invalid_pixels_retained": 0,
            "parameters": cfg.model_dump(),
        },
    )

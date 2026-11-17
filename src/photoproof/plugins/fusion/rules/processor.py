"""Processor wiring for fusion.rules.

Combines normalized evidence layers from ``fusion.normalize`` into a fused
suspicion map and per-region evidence summaries using transparent,
documented rules. It is not a learned or probabilistic model — scores are
heuristic and explicitly labelled as such.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize import DETECTOR_FAMILIES
from photoproof.plugins.fusion.rules import (
    MEDIUM_HIGH_SCORE_THRESHOLD,
    STRENGTH_WEIGHTS,
)

#: Output contracts that carry a spatial map suitable for fusion.
_MAP_OUTPUTS = frozenset(
    {
        "compression.ela_map",
        "compression.dq_map",
        "compression.grid_map",
        "noise.sigma_map",
        "noise.consistency_map",
        "visual.sharpness_map",
        "visual.edge_width_map",
        "copymove.keypoints",
        "copymove.matches",
        "copymove.verification",
        "copymove.mask",
    }
)


def _as_map(values: Any) -> np.ndarray | None:
    """Coerce a layer value to a 2-D float32 map, or ``None`` if not a map."""
    if isinstance(values, np.ndarray):
        arr = np.asarray(values, dtype=np.float32)
        if arr.ndim == 2:
            return arr
        if arr.ndim == 1 and arr.size == 1:
            return arr.reshape(1, 1)
    elif isinstance(values, (list, tuple)):
        try:
            arr = np.asarray(values, dtype=np.float32)
            if arr.ndim == 2:
                return arr
        except (ValueError, TypeError):
            pass
    return None


def _resample_to_common_grid(maps: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Resample all maps to the smallest common grid (nearest-neighbor).

    Returns a new dict; input maps are not modified.
    """
    if not maps:
        return {}
    min_h = min(m.shape[0] for m in maps.values())
    min_w = min(m.shape[1] for m in maps.values())
    if min_h == 0 or min_w == 0:
        return {name: np.zeros((0, 0), dtype=np.float32) for name in maps}
    result: dict[str, np.ndarray] = {}
    for name, m in maps.items():
        if m.shape == (min_h, min_w):
            result[name] = m.copy()
        else:
            ys = np.clip((np.arange(min_h) * m.shape[0] // min_h).astype(int), 0, m.shape[0] - 1)
            xs = np.clip((np.arange(min_w) * m.shape[1] // min_w).astype(int), 0, m.shape[1] - 1)
            result[name] = m[np.ix_(ys, xs)].astype(np.float32)
    return result


def _noisy_or(scores: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted noisy-OR per pixel: ``1 - prod(1 - w_i * s_i)``.

    ``scores`` has shape ``(num_layers, h, w)``; ``weights`` has shape
    ``(num_layers,)``. Returns shape ``(h, w)``.
    """
    if scores.size == 0:
        return np.zeros(scores.shape[1:], dtype=np.float32)
    product = np.ones(scores.shape[1:], dtype=np.float32)
    for layer_idx in range(scores.shape[0]):
        product *= 1.0 - weights[layer_idx] * scores[layer_idx]
    return 1.0 - product


def _connected_components(mask: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Find 4-connected components of a boolean mask.

    Returns a list of (rows, cols) index arrays, one per component.
    """
    if mask.size == 0:
        return []
    h, w = mask.shape
    visited = np.zeros((h, w), dtype=bool)
    components: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(h):
        for j in range(w):
            if mask[i, j] and not visited[i, j]:
                # BFS
                stack = [(i, j)]
                visited[i, j] = True
                rows: list[int] = []
                cols: list[int] = []
                while stack:
                    ci, cj = stack.pop()
                    rows.append(ci)
                    cols.append(cj)
                    for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ni, nj = ci + di, cj + dj
                        if 0 <= ni < h and 0 <= nj < w and mask[ni, nj] and not visited[ni, nj]:
                            visited[ni, nj] = True
                            stack.append((ni, nj))
                components.append(
                    (
                        np.array(rows, dtype=int),
                        np.array(cols, dtype=int),
                    )
                )
    return components


def run(ctx: PluginContext) -> dict[str, object]:
    """Fuse normalized evidence layers into a suspicion map and region summaries.

    Reads ``fusion.layers`` and ``fusion.unavailable`` (optional inputs from
    ``fusion.normalize``). Produces ``fusion.map`` (heuristic suspicion score
    map in ``[0, 1]``) and ``fusion.evidence`` (per-region summaries).

    If no detector layers are available, ``fusion.map`` is ``None`` and
    ``fusion.evidence`` lists the unavailable detectors.
    """
    layers = ctx.inputs.get("fusion.layers", [])
    unavailable_raw = ctx.inputs.get("fusion.unavailable", [])
    unavailable = list(unavailable_raw) if isinstance(unavailable_raw, list) else []

    if not isinstance(layers, list):
        layers = []

    # Build per-detector maps from layers.
    layer_maps: dict[str, np.ndarray] = {}
    layer_info: dict[str, dict[str, object]] = {}
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        det_id = layer.get("detector_id")
        if not det_id:
            continue
        m = _as_map(layer.get("map"))
        if m is None:
            continue
        layer_maps[str(det_id)] = m
        layer_info[str(det_id)] = {
            "strength_class": layer.get("strength_class", "weak"),
            "regions": layer.get("regions", ()),
            "valid_mask": layer.get("valid_mask"),
            "notes": layer.get("notes", ()),
        }

    # If no maps at all, return empty result.
    if not layer_maps:
        return {
            "fusion.map": None,
            "fusion.evidence": [
                {
                    "detector_id": name,
                    "reason": "unavailable",
                    "score": 0.0,
                    "strength_class": "unknown",
                    "families": [],
                    "rules_fired": [],
                }
                for name in sorted(unavailable)
            ],
        }

    # Resample all maps to a common grid.
    common_maps = _resample_to_common_grid(layer_maps)
    h, w = next(iter(common_maps.values())).shape

    # Fused score map.
    fused = np.zeros((h, w), dtype=np.float32)

    # Group layers by strength class and family.

    strong_layers: list[tuple[str, np.ndarray]] = []
    medium_layers: list[tuple[str, np.ndarray]] = []
    weak_layers: list[tuple[str, np.ndarray]] = []
    for det_id, m in common_maps.items():
        sc = layer_info[det_id]["strength_class"]
        fam = DETECTOR_FAMILIES.get(det_id, "unknown")
        if sc == "strong":
            strong_layers.append((det_id, m))
        elif sc == "medium":
            medium_layers.append((det_id, m))
        elif sc == "weak":
            weak_layers.append((det_id, m))

    # Strong contribution: max over strong layers.
    if strong_layers:
        strong_stack = np.stack([m for _, m in strong_layers], axis=0)
        fused = np.maximum(fused, strong_stack.max(axis=0))

    # Per-pixel family support: which families have medium/strong > 0?
    support_families = np.zeros((h, w), dtype=object)
    for i in range(h):
        for j in range(w):
            fam_set: set[str] = set()
            for det_id, m in strong_layers + medium_layers:
                if m[i, j] > 0:
                    fam_set.add(DETECTOR_FAMILIES.get(det_id, "unknown"))
            support_families[i, j] = fam_set

    # Medium layers: gated if another family has support, or score > threshold.
    gated_medium_scores: list[np.ndarray] = []
    gated_medium_weights: list[float] = []
    for det_id, m in medium_layers:
        fam = DETECTOR_FAMILIES.get(det_id, "unknown")
        weight_val = STRENGTH_WEIGHTS["medium"]
        gated = np.zeros((h, w), dtype=np.float32)
        for i in range(h):
            for j in range(w):
                families = support_families[i, j]
                other_families = families - {fam}
                if other_families or m[i, j] > MEDIUM_HIGH_SCORE_THRESHOLD:
                    gated[i, j] = m[i, j]
        gated_medium_scores.append(gated)
        gated_medium_weights.append(weight_val)

    # Weak layers: gated if medium/strong support exists (from any family).
    gated_weak_scores: list[np.ndarray] = []
    gated_weak_weights: list[float] = []
    for det_id, m in weak_layers:
        fam = DETECTOR_FAMILIES.get(det_id, "unknown")
        weight_val = STRENGTH_WEIGHTS["weak"]
        gated = np.zeros((h, w), dtype=np.float32)
        for i in range(h):
            for j in range(w):
                if support_families[i, j]:
                    gated[i, j] = m[i, j]
        gated_weak_scores.append(gated)
        gated_weak_weights.append(weight_val)

    # Combine medium and weak via weighted noisy-OR.
    all_gated_scores = gated_medium_scores + gated_weak_scores
    all_gated_weights = gated_medium_weights + gated_weak_weights
    if all_gated_scores:
        score_stack = np.stack(all_gated_scores, axis=0)
        weight_arr = np.array(all_gated_weights, dtype=np.float32)
        # Weighted noisy-OR applied per pixel.
        product = np.ones((h, w), dtype=np.float32)
        for s, wgt in zip(score_stack, weight_arr, strict=False):
            product *= 1.0 - wgt * s
        combined = 1.0 - product
        fused = np.minimum(fused + combined, 1.0)

    # Clamp to [0, 1].
    fused = np.clip(fused, 0.0, 1.0)

    # Build region evidence summaries for suspicious regions (score >= 0.5).
    suspicious_mask = fused >= 0.5
    components = _connected_components(suspicious_mask)
    evidence: list[dict[str, object]] = []
    for rows, cols in components:
        region_rows = rows.tolist()
        region_cols = cols.tolist()
        region_scores = fused[rows, cols]
        max_score = float(region_scores.max()) if region_scores.size else 0.0
        # Contributing detectors: those with non-zero score in this region.
        contributing: list[str] = []
        rules_fired: list[str] = []
        families_involved: set[str] = set()
        for det_id, m in common_maps.items():
            region_det_scores = m[rows, cols]
            if np.any(region_scores > 0) and np.any(region_det_scores > 0):
                contributing.append(det_id)
                sc = layer_info[det_id]["strength_class"]
                fam = DETECTOR_FAMILIES.get(det_id, "unknown")
                families_involved.add(fam)
                if sc == "strong":
                    rules_fired.append(f"strong_evidence:{det_id}")
                elif sc == "medium":
                    rules_fired.append(f"medium_evidence:{det_id}")
                else:
                    rules_fired.append(f"weak_boost:{det_id}")
        # Unavailable detectors in this region's bounding box.
        for name in unavailable:
            if name not in layer_maps:
                families_involved.add(DETECTOR_FAMILIES.get(name, "unknown"))
                rules_fired.append(f"unavailable:{name}")
        evidence.append(
            {
                "region": {
                    "rows": region_rows,
                    "cols": region_cols,
                    "bbox": [
                        int(min(region_rows)),
                        int(min(region_cols)),
                        int(max(region_rows)),
                        int(max(region_cols)),
                    ],
                },
                "max_score": max_score,
                "contributing_detectors": sorted(contributing),
                "families": sorted(families_involved),
                "rules_fired": sorted(set(rules_fired)),
            }
        )

    # Always list unavailable detectors in evidence.
    for name in sorted(unavailable):
        if name not in layer_maps:
            evidence.append(
                {
                    "detector_id": name,
                    "reason": "unavailable",
                    "score": 0.0,
                    "strength_class": "unknown",
                    "families": [DETECTOR_FAMILIES.get(name, "unknown")],
                    "rules_fired": [f"unavailable:{name}"],
                }
            )

    return {
        "fusion.map": fused.tolist(),
        "fusion.evidence": evidence,
    }

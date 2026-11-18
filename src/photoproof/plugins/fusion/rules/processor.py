"""Processor wiring for fusion.rules.

Combines normalized evidence layers from ``fusion.normalize`` into a fused
suspicion map and per-region evidence summaries using transparent,
documented rules. It is not a learned or probabilistic model — scores are
heuristic and explicitly labelled as such.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize import DETECTOR_FAMILIES
from photoproof.plugins.fusion.rules.schema import Config

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


def _resample_to_common_grid(
    maps: dict[str, np.ndarray], *, global_layers: frozenset[str] = frozenset()
) -> dict[str, np.ndarray]:
    """Resample all maps to the smallest common grid (nearest-neighbor).

    Returns a new dict; input maps are not modified.
    """
    if not maps:
        return {}
    spatial = [m for name, m in maps.items() if name not in global_layers]
    grids = spatial or list(maps.values())
    min_h = min(m.shape[0] for m in grids)
    min_w = min(m.shape[1] for m in grids)
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
    global_layers: set[str] = set()
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        det_id = layer.get("detector_id")
        if not det_id:
            continue
        m = _as_map(layer.get("map"))
        if m is None:
            unavailable.append(str(det_id))
            continue
        if not m.size or not np.isfinite(m).all() or np.any((m < 0) | (m > 1)):
            raise InputRejectedError(
                f"fusion.rules: invalid score map for {det_id}; supply finite scores in [0, 1]"
            )
        valid = layer.get("valid_mask")
        if valid is not None:
            mask = np.asarray(valid)
            if mask.shape != m.shape or mask.dtype != np.bool_:
                raise InputRejectedError(
                    f"fusion.rules: invalid valid_mask for {det_id}; match the map with booleans"
                )
            m = np.where(mask, m, 0.0)
        notes = layer.get("notes", ())
        if isinstance(notes, (list, tuple)) and "scalar_normalized" in notes:
            if m.shape != (1, 1):
                raise InputRejectedError(
                    f"fusion.rules: global scalar {det_id} must have exactly one score"
                )
            global_layers.add(str(det_id))
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
            "fusion.score_info": {"label": "heuristic_score", "calibration": "uncalibrated"},
            "fusion.evidence": [
                {
                    "detector_id": name,
                    "reason": "unavailable",
                    "score": None,
                    "strength_class": "unknown",
                    "families": [],
                    "rules_fired": [],
                }
                for name in sorted(unavailable)
            ],
        }

    # Resample all maps to a common grid.
    common_maps = _resample_to_common_grid(layer_maps, global_layers=frozenset(global_layers))
    h, w = next(iter(common_maps.values())).shape

    config = ctx.config if isinstance(ctx.config, Config) else Config.model_validate(ctx.config)
    families = {name: DETECTOR_FAMILIES.get(name, "unknown") for name in common_maps}
    strengths = {name: layer_info[name]["strength_class"] for name in common_maps}
    weights = config.strength_weights
    strong = {
        name: m * weights.strong for name, m in common_maps.items() if strengths[name] == "strong"
    }
    medium = {name: m for name, m in common_maps.items() if strengths[name] == "medium"}
    accepted: dict[str, np.ndarray] = dict(strong)
    for name, m in medium.items():
        independent = np.zeros((h, w), dtype=bool)
        for other, values in {**strong, **medium}.items():
            if families[other] != families[name]:
                independent |= values > 0
        accepted[name] = np.where(
            independent | (m > config.medium_high_score_threshold), m * weights.medium, 0.0
        )
    supported = np.zeros((h, w), dtype=bool)
    support_level = np.zeros((h, w), dtype=np.float32)
    for values in accepted.values():
        supported |= values > 0
        support_level = np.maximum(support_level, values)
    for name, m in common_maps.items():
        if strengths[name] == "weak":
            accepted[name] = np.where(
                supported, m * weights.weak * config.weak_boost_factor * support_level, 0.0
            )
    # Correlated cues contribute a maximum once per family, never repeated OR terms.
    # Strong evidence combines by maximum; its family cannot boost it a second time.
    strong_max = np.zeros((h, w), dtype=np.float32)
    for values in strong.values():
        strong_max = np.maximum(strong_max, values)
    family_scores: list[np.ndarray] = []
    for family in sorted(set(families.values())):
        score = np.zeros((h, w), dtype=np.float32)
        has_strong = np.zeros((h, w), dtype=bool)
        for name, values in strong.items():
            if families[name] == family:
                has_strong |= values > 0
        for name, values in accepted.items():
            if families[name] == family and name not in strong:
                score = np.maximum(score, values)
        family_scores.append(np.where(has_strong, 0.0, score))
        for name, values in accepted.items():
            if families[name] != family:
                continue
            if name in strong:
                accepted[name] = np.where(values == strong_max, values, 0.0)
            else:
                accepted[name] = np.where(~has_strong & (values == score), values, 0.0)
    product = np.ones((h, w), dtype=np.float32)
    for score in family_scores:
        product *= 1.0 - score
    fused = np.clip(1.0 - (1.0 - strong_max) * product, 0.0, 1.0)

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
        for det_id, m in accepted.items():
            region_det_scores = m[rows, cols]
            if np.any(region_scores > 0) and np.any(region_det_scores > 0):
                contributing.append(det_id)
                if det_id in global_layers:
                    rules_fired.append(f"global_context:{det_id}")
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
                "global_context_detectors": sorted(set(contributing) & global_layers),
                "detector_scores": {
                    name: float(common_maps[name][rows, cols].max())
                    for name in sorted(contributing)
                },
                "weighted_contributions": {
                    name: float(accepted[name][rows, cols].max()) for name in sorted(contributing)
                },
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
                    "score": None,
                    "strength_class": "unknown",
                    "families": [DETECTOR_FAMILIES.get(name, "unknown")],
                    "rules_fired": [f"unavailable:{name}"],
                }
            )

    return {
        "fusion.map": fused.tolist(),
        "fusion.evidence": evidence,
        "fusion.score_info": {"label": "heuristic_score", "calibration": "uncalibrated"},
    }

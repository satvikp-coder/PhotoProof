"""Production entry point for source-bound binary evidence.

The registered run uses the coordinated native implementation. Retained private
numerical helpers are historical mathematical controls, not alternate wire readers.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.rules.native_processor import run_native

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
    """Run the source-bound binary production pipeline."""
    return run_native(ctx)

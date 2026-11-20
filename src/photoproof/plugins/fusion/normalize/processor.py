"""Production entry point for source-bound binary evidence.

The registered run uses the coordinated native implementation. Retained private
numerical helpers are historical mathematical controls, not alternate wire readers.
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize import (
    UNCALIBRATED,
)
from photoproof.plugins.fusion.normalize.native_processor import normalize_native

#: Output contracts that carry a spatial map suitable for normalization to [0, 1].
_MAP_OUTPUTS = frozenset(
    {
        "compression.ela_map",
        "compression.dq_map",
        "compression.grid_map",
        "noise.sigma_map",
        "noise.consistency_map",
        "visual.sharpness_map",
        "visual.edge_width_map",
    }
)


def _logistic_squash(values: np.ndarray, z0: float, k: float) -> np.ndarray:
    """Apply logistic squashing ``s = 1 / (1 + exp(-(z - z0) / k))`` to z-scores.

    Clips the exponent argument to avoid overflow. Returns values in ``(0, 1)``.
    """
    exponent = np.clip(-(values - z0) / k, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(exponent))


def _normalize_map(values: np.ndarray, z0: float, k: float) -> np.ndarray:
    """Normalize a detector map to ``[0, 1]`` via robust z-score + logistic squash.

    Non-finite values are replaced with 0 before normalization.
    """
    clean = np.where(np.isfinite(values), values, 0.0).astype(np.float32)
    if clean.size == 0:
        return clean
    median = float(np.median(clean))
    mad = float(np.median(np.abs(clean - median)))
    scale = 1.4826 * mad
    if scale == 0.0:
        # Constant map: everything is at the median, so no anomaly.
        return np.zeros_like(clean, dtype=np.float32)
    z = (clean - median) / scale
    return _logistic_squash(z, z0, k).astype(np.float32)


def run(ctx: PluginContext) -> dict[str, object]:
    """Run the source-bound binary production pipeline."""
    return normalize_native(ctx)


def _layer_for_output(name: str, value: Any, cfg: dict[str, object]) -> dict[str, object] | None:
    """Build one EvidenceLayer dict for a detector output."""
    z0 = float(cast(float, cfg["z0"]))
    k = float(cast(float, cfg["k"]))
    strength_class = cast(str, cfg["strength_class"])

    regions: tuple[tuple[int, int, int, int], ...] = ()
    if isinstance(value, (np.ndarray, list, tuple)):
        try:
            values = np.asarray(value, dtype=np.float32)
        except (TypeError, ValueError, OverflowError):
            return None
        if values.ndim != 2 or not values.size:
            return None
        if not np.isfinite(values).all():
            raise InputRejectedError(
                f"fusion.normalize: nonfinite map for {name}; inspect the detector output"
            )
        if cfg.get("method") == "identity":
            if np.any((values < 0) | (values > 1)):
                raise InputRejectedError(
                    f"fusion.normalize: invalid unit-interval map for {name}; inspect the detector"
                )
            map_arr = values.copy()
        else:
            map_arr = _normalize_map(values, z0, k)
        valid_mask = np.ones(map_arr.shape, dtype=bool)
        notes = ("map_normalized",)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not np.isfinite(value):
            raise InputRejectedError(
                f"fusion.normalize: nonfinite score for {name}; inspect the detector output"
            )
        if cfg.get("method") == "identity":
            if not 0 <= value <= 1:
                raise InputRejectedError(
                    f"fusion.normalize: invalid unit-interval score for {name}; "
                    "inspect the detector"
                )
            score = float(value)
        else:
            score = float(_logistic_squash(np.array([float(value)]), z0, k)[0])
        map_arr = np.array([[score]], dtype=np.float32)
        valid_mask = np.ones((1, 1), dtype=bool)
        notes = ("scalar_normalized",)
    else:
        # An unread artifact reference, null or unsupported structured output is
        # unavailable. No filesystem read or fabricated zero-score layer is allowed.
        return None

    return {
        "detector_id": name,
        "map": map_arr.tolist(),
        "regions": regions,
        "strength_class": strength_class,
        "valid_mask": valid_mask.tolist(),
        "calibration": UNCALIBRATED,
        "notes": notes,
    }

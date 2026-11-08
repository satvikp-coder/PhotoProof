"""Processor wiring for the fusion.normalize plugin.

Reads every detector output that is present in the run (declared as optional
inputs), maps each to an :class:`~photoproof.contracts.evidence.EvidenceLayer`
with a score in ``[0, 1]``, and marks absent detectors as ``unavailable`` —
never as zero-score layers.
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize import (
    DETECTOR_NORMALIZATION,
    UNCALIBRATED,
)

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
    """Normalize all present detector outputs into EvidenceLayer objects.

    Reads each optional input that is present in ``ctx.inputs``. Map-type outputs
    are normalized to ``[0, 1]`` via robust z-scoring and logistic squashing.
    Scalar outputs are passed through as single-element maps.

    Detectors that are not present in the run are recorded as ``unavailable``
    with no layer produced (never a zero-score layer).
    """
    layers: list[dict[str, object]] = []
    unavailable: list[str] = []

    # Every key in ctx.inputs is an optional input that the run resolved.
    for name in sorted(ctx.inputs):
        value = ctx.inputs[name]
        cfg = DETECTOR_NORMALIZATION.get(name)
        if cfg is None:
            # Known output but no normalization config: skip with a note.
            continue

        layer = _layer_for_output(name, value, cfg)
        layers.append(layer)

    # Any known detector output not in ctx.inputs is absent/unavailable.
    for name in DETECTOR_NORMALIZATION:
        if name not in ctx.inputs:
            unavailable.append(name)

    return {
        "fusion.layers": layers,
        "fusion.unavailable": tuple(sorted(unavailable)),
    }


def _layer_for_output(name: str, value: Any, cfg: dict[str, object]) -> dict[str, object]:
    """Build one EvidenceLayer dict for a detector output."""
    z0 = float(cast(float, cfg["z0"]))
    k = float(cast(float, cfg["k"]))
    strength_class = cast(str, cfg["strength_class"])

    if isinstance(value, np.ndarray):
        map_arr = _normalize_map(value, z0, k)
        regions: tuple[tuple[int, int, int, int], ...] = ()
        valid_mask = np.ones(map_arr.shape, dtype=bool)
        notes = ("map_normalized",)
    elif isinstance(value, (int, float)):
        # Scalar summary output: single-cell map.
        s = _logistic_squash(np.array([float(value)]), z0, k)[0]
        map_arr = np.array([[s]], dtype=np.float32)
        regions = ()
        valid_mask = np.ones((1, 1), dtype=bool)
        notes = ("scalar_normalized",)
    elif isinstance(value, dict):
        # ArtifactRef or structured output: cannot normalize directly.
        map_arr = np.zeros((1, 1), dtype=np.float32)
        regions = ()
        valid_mask = np.zeros((1, 1), dtype=bool)
        notes = ("artifact_reference",)
    else:
        map_arr = np.zeros((1, 1), dtype=np.float32)
        regions = ()
        valid_mask = np.zeros((1, 1), dtype=bool)
        notes = ("unrecognized_output",)

    return {
        "detector_id": name,
        "map": map_arr.tolist(),
        "regions": regions,
        "strength_class": strength_class,
        "valid_mask": valid_mask.tolist(),
        "calibration": UNCALIBRATED,
        "notes": notes,
    }


def _schema_default() -> dict[str, object]:
    return {}


# Expose Config for schema resolution (config_schema = "schema:Config").
Config = dict  # placeholder; no config fields beyond DETECTOR_NORMALIZATION

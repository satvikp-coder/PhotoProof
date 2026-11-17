"""Pure-Python JPEG 8x8 grid misalignment algorithm.

Implements Blocking Artifact Grid (BAG) extraction (Li, Yuan & Yu 2009):
compute second-order differences across pixel rows and columns, accumulate
periodic energy at period 8, and find the phase. Locally, compare the energy
at the dominant phase to the best alternative phase per window.

Works on the luminance channel of the decoded image. Blocking artifacts survive
format conversion (e.g., JPEG → PNG), so this applies even to non-JPEG images
that were previously JPEG-compressed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.stats import AnomalyMap, anomaly_map, robust_zscore

#: Grid period (8x8 JPEG blocks)
_BLOCK = 8


@dataclass(frozen=True)
class GridOrigin:
    """Grid origin estimate with confidence."""

    dx: int  # x offset in 0..7
    dy: int  # y offset in 0..7
    confidence: float  # 0..1, higher = more confident


@dataclass(frozen=True)
class GridResult:
    """Grid analysis result."""

    origin: GridOrigin
    anomaly: AnomalyMap  # per-window grid mismatch scores
    context: str  # "jpeg" | "non_jpeg" | "unknown"


def _second_order_difference(arr: np.ndarray, axis: int) -> np.ndarray:
    """Compute second-order difference along an axis.

    For axis 0 (vertical): d2[i, j] = arr[i+1, j] - 2*arr[i, j] + arr[i-1, j]
    For axis 1 (horizontal): d2[i, j] = arr[i, j+1] - 2*arr[i, j] + arr[i, j-1]
    """
    if axis == 0:
        # Vertical second difference
        d2 = np.zeros_like(arr, dtype=np.float32)
        d2[1:-1, :] = arr[2:, :] - 2.0 * arr[1:-1, :] + arr[:-2, :]
        return d2
    else:
        # Horizontal second difference
        d2 = np.zeros_like(arr, dtype=np.float32)
        d2[:, 1:-1] = arr[:, 2:] - 2.0 * arr[:, 1:-1] + arr[:, :-2]
        return d2


def _compute_blocking_energy(d2: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Compute blocking energy at a given grid phase (dx, dy).

    For each pixel, we look at the second-order difference and weight it by
    how well it aligns with the grid phase. Blocking artifacts create periodic
    energy at period 8 with a specific phase.
    """
    h, w = d2.shape
    energy = np.zeros((h, w), dtype=np.float32)

    # For each pixel, check if it aligns with grid lines at (dx, dy) mod 8
    # Grid lines occur at x ≡ dx (mod 8) and y ≡ dy (mod 8)
    # Blocking artifacts appear as strong second differences near grid lines

    # Create coordinate grids
    yy, xx = np.mgrid[0:h, 0:w]

    # Mask for pixels near vertical grid lines (x ≡ dx mod 8)
    v_mask = (xx % _BLOCK) == dx
    # Mask for pixels near horizontal grid lines (y ≡ dy mod 8)
    h_mask = (yy % _BLOCK) == dy

    # Blocking artifacts produce strong second differences at grid lines
    # Use absolute second difference as energy measure
    abs_d2 = np.abs(d2)

    # Energy is high where second differences are strong AND aligned with grid
    grid_mask = v_mask | h_mask
    energy[grid_mask] = abs_d2[grid_mask]

    return energy


def _compute_phase_energy_map(
    d2: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute energy map for each phase (dx, dy).

    Returns:
        energy_maps: array of shape (8, 8, h, w) with energy at each phase
        total_energy: array of shape (h, w) with total energy across all phases
    """
    h, w = d2.shape
    energy_maps = np.zeros((8, 8, h, w), dtype=np.float32)

    for dx in range(_BLOCK):
        for dy in range(_BLOCK):
            energy_maps[dx, dy] = _compute_blocking_energy(d2, dx, dy)

    total_energy = energy_maps.sum(axis=(0, 1))
    return energy_maps, total_energy


def _find_dominant_phase(
    energy_maps: np.ndarray,
) -> tuple[int, int, float]:
    """Find the phase (dx, dy) with maximum total energy.

    Returns (dx, dy, confidence) where confidence is the ratio of dominant
    phase energy to total energy.
    """
    # Sum energy over all pixels for each phase
    phase_totals = energy_maps.sum(axis=(2, 3))  # shape (8, 8)

    # Find dominant phase
    flat_idx = np.argmax(phase_totals)
    dx, dy = np.unravel_index(flat_idx, (8, 8))

    total = phase_totals.sum()
    dominant = phase_totals[dx, dy]

    confidence = 0.0 if total == 0 else float(dominant / total)

    return int(dx), int(dy), confidence


def _compute_local_mismatch_map(
    d2: np.ndarray,
    energy_maps: np.ndarray,
    window: int,
    stride: int,
) -> np.ndarray:
    """Compute per-window grid mismatch map.

    For each window, compute the energy at the dominant phase vs the best
    alternative phase. High mismatch = strong evidence of grid misalignment
    (e.g., pasted content from a differently-aligned JPEG).
    """
    h, w = d2.shape
    out_h = (h - window) // stride + 1
    out_w = (w - window) // stride + 1

    mismatch = np.zeros((out_h, out_w), dtype=np.float32)

    for i in range(out_h):
        for j in range(out_w):
            y0 = i * stride
            x0 = j * stride
            window_energy = energy_maps[:, :, y0 : y0 + window, x0 : x0 + window]

            # Total energy per phase in this window
            phase_totals = window_energy.sum(axis=(2, 3))  # shape (8, 8)

            if phase_totals.sum() == 0:
                mismatch[i, j] = 0.0
                continue

            # Dominant phase energy
            dominant = phase_totals.max()

            # Best alternative phase (exclude dominant)
            alt_mask = np.ones((8, 8), dtype=bool)
            flat_idx = np.argmax(phase_totals)
            alt_mask.flat[flat_idx] = False
            alt_max = phase_totals[alt_mask].max() if alt_mask.any() else 0.0

            # Mismatch = dominant - alternative (normalized)
            if dominant > 0:
                mismatch[i, j] = (dominant - alt_max) / dominant
            else:
                mismatch[i, j] = 0.0

    return mismatch


def analyze_jpeg_grid(
    image_array: np.ndarray,
    *,
    window: int = 64,
    stride: int = 32,
) -> GridResult:
    """Run JPEG grid misalignment analysis on an image array.

    Returns a :class:`GridResult` with the estimated grid origin and per-window
    mismatch map.

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(image_array, np.ndarray):
        raise InputRejectedError(f"expected a NumPy ndarray, got {type(image_array).__name__}")
    if image_array.ndim not in (2, 3):
        raise InputRejectedError(f"expected a 2-D or 3-D array, got {image_array.ndim} dimensions")
    if image_array.size == 0:
        raise InputRejectedError("image array is empty")

    # Convert to grayscale luminance
    if image_array.ndim == 2:
        lum = image_array.astype(np.float32)
    elif image_array.shape[2] == 1:
        lum = image_array[:, :, 0].astype(np.float32)
    elif image_array.shape[2] == 3:
        # BT.601 luma
        lum = (
            0.299 * image_array[:, :, 0].astype(np.float32)
            + 0.587 * image_array[:, :, 1].astype(np.float32)
            + 0.114 * image_array[:, :, 2].astype(np.float32)
        )
    elif image_array.shape[2] == 4:
        lum = image_array[:, :, 0].astype(np.float32)
    else:
        raise InputRejectedError(f"unsupported channel count: {image_array.shape[2]}")

    _, _w = lum.shape

    # Compute second-order differences
    d2_v = _second_order_difference(lum, axis=0)
    d2_h = _second_order_difference(lum, axis=1)
    d2 = np.abs(d2_v) + np.abs(d2_h)

    # Compute energy maps for each phase
    energy_maps, _total_energy = _compute_phase_energy_map(d2)

    # Find dominant phase
    dx, dy, confidence = _find_dominant_phase(energy_maps)

    # Compute local mismatch map
    mismatch = _compute_local_mismatch_map(d2, energy_maps, window, stride)

    # Convert mismatch to robust z-scores for anomaly map
    if mismatch.size > 0:
        z_scores, degenerate = robust_zscore(mismatch)
    else:
        z_scores = mismatch
        degenerate = True

    anomaly = anomaly_map(
        values=z_scores,
        block=(window, window),
        stride=(stride, stride),
        notes=("degenerate",) if degenerate else (),
    )

    # Determine context based on confidence
    if confidence < 0.3:
        context = "low_confidence"
    elif confidence < 0.6:
        context = "moderate"
    else:
        context = "high_confidence"

    origin = GridOrigin(dx=dx, dy=dy, confidence=confidence)

    return GridResult(
        origin=origin,
        anomaly=anomaly,
        context=context,
    )


def grid_result_to_dict(result: GridResult) -> dict[str, Any]:
    """Convert a :class:`GridResult` to a plain dict for plugin output."""
    return {
        "origin": {
            "dx": result.origin.dx,
            "dy": result.origin.dy,
            "confidence": result.origin.confidence,
        },
        "context": result.context,
        "anomaly": {
            "values": result.anomaly.values.tolist(),
            "block": list(result.anomaly.block),
            "stride": list(result.anomaly.stride),
            "scale": result.anomaly.scale,
            "valid_mask": result.anomaly.valid_mask.tolist(),
            "notes": list(result.anomaly.notes),
        },
    }

"""Pure NumPy/OpenCV sharpness analysis using Laplacian variance.

Implements the Variance of the Laplacian focus measure (Pech-Pacheco et al. 2000)
over local blocks, with optional contrast normalization and robust-z inconsistency
detection. This is a weak visual cue for copy-move forgery detection.

Extracted from issue #25 - measure local Laplacian sharpness inconsistency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

#: Small epsilon to avoid division by zero in contrast normalization.
_EPS = 1e-6


@dataclass(frozen=True)
class SharpnessResult:
    """Sharpness analysis result."""

    sharpness_map: np.ndarray  # (H, W) float32: log variance of Laplacian per block
    anomaly_map: np.ndarray  # (H, W) float32: robust-z inconsistency map
    block_size: int
    config: dict[str, Any]  # Configuration used


def _laplacian_variance(block: np.ndarray) -> float:
    """Compute variance of Laplacian for a single block."""
    lap = cv2.Laplacian(block, cv2.CV_32F, ksize=3)
    return float(np.var(lap))


def _block_intensity_variance(block: np.ndarray) -> float:
    """Compute intensity variance for a single block."""
    return float(np.var(block.astype(np.float64)))


def analyze_sharpness(
    image: np.ndarray,
    *,
    block: int = 32,
    contrast_normalize: bool = True,
) -> dict[str, Any]:
    """Analyze local sharpness using Laplacian variance.

    Divides the image into blocks and computes the log variance of the
    Laplacian for each block. Optionally normalizes by local contrast to
    separate blur from low-contrast content. Then computes a robust-z
    inconsistency map to flag blocks whose sharpness is inconsistent with
    their neighbors.

    Returns a dict with:
        - sharpness_map: (H, W) float32: per-block log Laplacian variance,
          tiled to full image resolution
        - anomaly_map: (H, W) float32: robust-z score per block,
          tiled to full image resolution
        - block_size: int
        - config: dict with configuration used

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(image, np.ndarray):
        raise InputRejectedError(f"image must be a NumPy ndarray, got {type(image).__name__}")
    if image.ndim not in (2, 3):
        raise InputRejectedError(f"image must have 2 or 3 dimensions, got {image.ndim}")

    # Convert to luminance if needed
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    else:
        gray = image.astype(np.float32)

    h, w = gray.shape

    # Compute per-block sharpness
    n_blocks_y = max(1, h // block)
    n_blocks_x = max(1, w // block)

    sharpness_values: list[float] = []
    block_positions: list[tuple[int, int, int, int]] = []

    for by in range(n_blocks_y):
        for bx in range(n_blocks_x):
            y0 = by * block
            x0 = bx * block
            y1 = min(h, y0 + block)
            x1 = min(w, x0 + block)
            block_img = gray[y0:y1, x0:x1]

            if block_img.size == 0:
                continue

            lap_var = _laplacian_variance(block_img)

            if contrast_normalize:
                intensity_var = _block_intensity_variance(block_img)
                if intensity_var > _EPS:
                    lap_var = lap_var / (intensity_var + _EPS)

            # Log variance (handle zero/negative)
            log_var = float(np.log(max(lap_var, _EPS)))

            sharpness_values.append(log_var)
            block_positions.append((y0, x0, y1, x1))

    sharpness_arr = np.array(sharpness_values, dtype=np.float32)

    # Compute robust-z inconsistency
    if len(sharpness_arr) > 1:
        median = float(np.median(sharpness_arr))
        mad = float(np.median(np.abs(sharpness_arr - median)))
        # Robust z-score: 0.6745 * (x - median) / MAD
        if mad > _EPS:
            z_scores = 0.6745 * (sharpness_arr - median) / mad
        else:
            z_scores = np.zeros_like(sharpness_arr)
    else:
        median = float(sharpness_arr[0]) if len(sharpness_arr) > 0 else 0.0
        z_scores = np.zeros_like(sharpness_arr)

    # Tile maps back to full resolution
    sharpness_map = np.zeros((h, w), dtype=np.float32)
    anomaly_map = np.zeros((h, w), dtype=np.float32)

    for i, (y0, x0, y1, x1) in enumerate(block_positions):
        sharpness_map[y0:y1, x0:x1] = sharpness_arr[i]
        anomaly_map[y0:y1, x0:x1] = z_scores[i]

    config: dict[str, Any] = {
        "block": block,
        "contrast_normalize": contrast_normalize,
        "opencv_version": cv2.__version__,
    }

    return {
        "sharpness_map": sharpness_map,
        "anomaly_map": anomaly_map,
        "block_size": int(block),
        "config": config,
    }


def sharpness_result_to_dict(result: dict[str, Any]) -> dict[str, Any]:
    """Convert analysis result to plain dict for plugin output."""
    return {
        "sharpness_map": result["sharpness_map"].tolist(),
        "anomaly_map": result["anomaly_map"].tolist(),
        "block_size": result["block_size"],
        "config": result["config"],
    }

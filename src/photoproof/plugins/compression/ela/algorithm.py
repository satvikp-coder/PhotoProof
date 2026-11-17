"""Pure Error Level Analysis algorithm.

ELA recompresses an image at a controlled JPEG quality and computes the absolute
difference between the original and recompressed pixels. Regions that have undergone
different compression histories show different error levels.

This module is deterministic and side-effect free: it operates only on in-memory
NumPy arrays and uses Pillow for in-memory JPEG encoding (never writes to disk).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.stats import AnomalyMap, anomaly_map, block_reduce, robust_zscore

#: Scale factor for converting the ELA error map to 8-bit for artifact storage.
_ELA_SCALE = 255.0

#: Default JPEG quality for recompression.
_DEFAULT_QUALITY = 95

#: Default block size for anomaly map.
_DEFAULT_BLOCK = 16


@dataclass(frozen=True)
class ElaResult:
    """ELA computation result."""

    ela_map: np.ndarray  # (H, W) uint8, scaled error map
    error_map: np.ndarray  # (H, W) float32, per-pixel error
    anomaly: AnomalyMap  # per-block robust z-score map
    quality: int
    aggregate: str
    block: int
    context: str  # "jpeg" | "non_jpeg"
    interpretation: str  # human-readable interpretation note


def _rgb_to_gray_for_error(arr: np.ndarray) -> np.ndarray:
    """Convert RGB array to single-channel using BT.601 luma weights."""
    if arr.ndim == 2:
        return arr.astype(np.float32)
    weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    return np.tensordot(arr.astype(np.float32), weights, axes=([2], [0]))


def _recompress_jpeg(arr: np.ndarray, quality: int) -> np.ndarray:
    """Recompress an RGB array as JPEG at ``quality`` and return decoded pixels.

    Uses Pillow in-memory encoding (``io.BytesIO``), never writes to disk.
    Deterministic encoder settings: ``subsampling=0``, ``optimize=False``.
    """
    pil = Image.fromarray(arr, mode="RGB")
    buffer = io.BytesIO()
    pil.save(buffer, format="JPEG", quality=quality, subsampling=0, optimize=False)
    buffer.seek(0)
    recompressed = Image.open(buffer).convert("RGB")
    return np.asarray(recompressed)


def _aggregate_channels(error: np.ndarray, aggregate: str) -> np.ndarray:
    """Aggregate per-channel error to a single channel."""
    if error.ndim == 2:
        return error
    if aggregate == "max":
        return np.max(error, axis=2)
    return np.mean(error, axis=2)


def _edge_suppression(error_map: np.ndarray, block: int) -> np.ndarray:
    """Divide error by local gradient energy to reduce texture false positives.

    Computes a Sobel gradient magnitude map and divides the error by it
    (with a small epsilon to avoid division by zero). High-texture regions
    naturally have high gradient energy, which suppresses their ELA scores.
    """
    if error_map.ndim != 2:
        return error_map

    h, w = error_map.shape
    if h < 3 or w < 3:
        return error_map

    # Sobel kernels
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)

    # Pad and compute gradients
    padded = np.pad(error_map, 1, mode="edge")
    gx = np.zeros((h, w), dtype=np.float32)
    gy = np.zeros((h, w), dtype=np.float32)

    for i in range(h):
        for j in range(w):
            window = padded[i : i + 3, j : j + 3]
            gx[i, j] = np.sum(window * kx)
            gy[i, j] = np.sum(window * ky)

    gradient = np.sqrt(gx**2 + gy**2)
    epsilon = 1.0
    suppressed = error_map / (gradient + epsilon)
    return suppressed


def analyze_ela(
    image_array: np.ndarray,
    *,
    quality: int = _DEFAULT_QUALITY,
    aggregate: str = "max",
    block: int = _DEFAULT_BLOCK,
    edge_suppression: bool = True,
) -> ElaResult:
    """Run ELA analysis on an image array.

    Returns an :class:`ElaResult` with the error map, anomaly map, and context.

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(image_array, np.ndarray):
        raise InputRejectedError(f"expected a NumPy ndarray, got {type(image_array).__name__}")
    if image_array.ndim not in (2, 3):
        raise InputRejectedError(f"expected a 2-D or 3-D array, got {image_array.ndim} dimensions")
    if image_array.size == 0:
        raise InputRejectedError("image array is empty")

    # Determine context from array characteristics (we don't have format info here)
    # ELA is meaningful primarily for JPEG inputs; callers set context.
    context = "jpeg"

    # Convert to RGB for recompression
    if image_array.ndim == 2:
        rgb = np.stack([image_array] * 3, axis=2)
    elif image_array.shape[2] == 1:
        rgb = np.stack([image_array[:, :, 0]] * 3, axis=2)
    elif image_array.shape[2] == 3:
        rgb = image_array
    elif image_array.shape[2] == 4:
        rgb = image_array[:, :, :3]
    else:
        raise InputRejectedError(f"unsupported channel count: {image_array.shape[2]}")

    # Ensure uint8
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)

    # Recompress
    recompressed = _recompress_jpeg(rgb, quality)

    # Compute per-pixel absolute error
    if rgb.shape != recompressed.shape:
        raise InputRejectedError(f"recompressed shape {recompressed.shape} != original {rgb.shape}")

    error = np.abs(rgb.astype(np.float32) - recompressed.astype(np.float32))

    # Aggregate channels
    error_map = _aggregate_channels(error, aggregate)

    # Optional edge suppression
    if edge_suppression:
        error_map = _edge_suppression(error_map, block)

    # Scale to 8-bit for artifact
    ela_max = float(error_map.max()) if error_map.size > 0 else 0.0
    if ela_max > 0:
        ela_map = np.clip(error_map / ela_max * _ELA_SCALE, 0, 255).astype(np.uint8)
    else:
        ela_map = np.zeros_like(error_map, dtype=np.uint8)

    # Build anomaly map via block reduction + robust z-score
    block_means = block_reduce(error_map, block=(block, block), func=lambda b: float(np.mean(b)))
    z_scores, degenerate = robust_zscore(block_means)

    anomaly = anomaly_map(
        values=z_scores,
        block=(block, block),
        stride=(block, block),
        notes=("degenerate",) if degenerate else (),
    )

    interpretation = (
        "ELA is supporting evidence only, never proof of forgery. Bright "
        "regions indicate areas that respond differently to recompression, "
        "which can indicate manipulation but can also result from texture, "
        "edges, sharpening, or normal compression. Never interpret ELA alone "
        "as proof of forgery."
    )

    return ElaResult(
        ela_map=ela_map,
        error_map=error_map,
        anomaly=anomaly,
        quality=quality,
        aggregate=aggregate,
        block=block,
        context=context,
        interpretation=interpretation,
    )


def ela_result_to_dict(result: ElaResult) -> dict[str, Any]:
    """Convert an :class:`ElaResult` to a plain dict for plugin output."""
    return {
        "strength": "weak",
        "context": result.context,
        "interpretation": result.interpretation,
        "quality": result.quality,
        "aggregate": result.aggregate,
        "block": result.block,
        "anomaly": {
            "values": result.anomaly.values.tolist(),
            "block": result.anomaly.block,
            "stride": result.anomaly.stride,
            "scale": result.anomaly.scale,
            "valid_mask": result.anomaly.valid_mask.tolist(),
            "notes": list(result.anomaly.notes),
        },
    }

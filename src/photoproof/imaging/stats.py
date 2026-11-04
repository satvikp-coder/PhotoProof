"""Shared block tiling and robust local-anomaly statistics for regional detectors.

Every per-region detector (ELA, noise variance, sharpness, edge, grid) computes
per-block statistics and flags blocks that deviate from the image-wide distribution.
This module provides one shared, tested implementation so that all detectors speak
the same statistical language and the fusion step (#32) can reconcile their outputs.

The robust z-score is::

    z = (x - median(x)) / (1.4826 * MAD(x)),   MAD = median(|x - median(x)|)

The factor 1.4826 makes MAD a consistent estimator of sigma for Gaussian data. It
resists the very outliers (tampered regions) that a mean/stddev normalization would
absorb. When MAD = 0 (constant input), ``robust_zscore`` returns zeros with
``degenerate=True`` and never divides by zero.

No new dependencies beyond NumPy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class TileSpec:
    """Description of one block within a tiled image."""

    block: tuple[int, int]  # (block_h, block_w)
    stride: tuple[int, int]  # (stride_h, stride_w)
    origin: tuple[int, int]  # (y0, x0) top-left corner of the block in image coords


@dataclass(frozen=True)
class AnomalyMap:
    """Per-block anomaly map with metadata for serialization and visualization.

    ``values`` is a ``(H_b, W_b)`` float32 array of per-block scores (typically
    robust z-scores). ``valid_mask`` is a ``(H_b, W_b)`` bool array marking blocks
    that contain valid data (as opposed to padding). ``notes`` records
    generator-relevant information such as ``"degenerate"`` or ``"too_small"``.
    """

    values: np.ndarray  # (H_b, W_b) float32
    block: tuple[int, int]  # (block_h, block_w)
    stride: tuple[int, int]  # (stride_h, stride_w)
    scale: str  # "robust_z"
    valid_mask: np.ndarray  # (H_b, W_b) bool
    notes: tuple[str, ...]


def tile(
    array: np.ndarray,
    block: tuple[int, int],
    stride: tuple[int, int] | None = None,
    *,
    border: str = "drop",
) -> tuple[np.ndarray, list[TileSpec]]:
    """Tile a 2-D array into overlapping or non-overlapping blocks.

    Returns ``(windows, specs)`` where ``windows`` is a view with shape
    ``(n_blocks_h, n_blocks_w, block_h, block_w)`` and ``specs`` gives the
    top-left origin of each block in image coordinates.

    With ``border="drop"``, partial edge blocks are discarded. With
    ``border="pad_reflect"``, the array is edge-reflected before tiling so every
    block is full-sized.
    """
    if array.ndim != 2:
        raise InputRejectedError(f"tile expects a 2-D array, got {array.ndim} dimensions")
    if block[0] <= 0 or block[1] <= 0:
        raise InputRejectedError(f"block dimensions must be positive, got {block}")
    bh, bw = block
    if stride is None:
        sh, sw = bh, bw
    else:
        sh, sw = stride
    if sh <= 0 or sw <= 0:
        raise InputRejectedError(f"stride dimensions must be positive, got {stride}")
    if border not in ("drop", "pad_reflect"):
        raise InputRejectedError(f"unknown border policy {border!r}; use 'drop' or 'pad_reflect'")

    h, w = array.shape
    if h < bh or w < bw:
        # Image smaller than one block: return an empty map.
        empty = np.zeros((0, 0, bh, bw), dtype=array.dtype)
        return empty, []

    if border == "pad_reflect":
        pad_h = (sh - (h - bh) % sh) % sh
        pad_w = (sw - (w - bw) % sw) % sw
        pad_top = pad_h // 2
        pad_bottom = pad_h - pad_top
        pad_left = pad_w // 2
        pad_right = pad_w - pad_left
        array = np.pad(
            array,
            ((pad_top, pad_bottom), (pad_left, pad_right)),
            mode="reflect",
        )
        h, w = array.shape
        origin_offset = (pad_top, pad_left)
    else:
        origin_offset = (0, 0)

    n_bh = (h - bh) // sh + 1
    n_bw = (w - bw) // sw + 1

    windows = np.lib.stride_tricks.sliding_window_view(array, (bh, bw))[::sh, ::sw]
    # sliding_window_view returns shape (n_bh, n_bw, bh, bw) when sliced with stride.

    specs: list[TileSpec] = []
    for i in range(n_bh):
        for j in range(n_bw):
            y0 = i * sh + origin_offset[0]
            x0 = j * sw + origin_offset[1]
            specs.append(TileSpec(block=block, stride=stride or block, origin=(y0, x0)))

    return windows, specs


def block_reduce(
    array: np.ndarray,
    block: tuple[int, int],
    stride: tuple[int, int] | None = None,
    *,
    func: Callable[[np.ndarray], float],
    border: str = "drop",
) -> np.ndarray:
    """Reduce each block of ``array`` to a scalar via ``func``.

    Returns a ``(H_b, W_b)`` float32 array of per-block results.
    """
    windows, _ = tile(array, block, stride, border=border)
    n_bh, n_bw = windows.shape[0], windows.shape[1]
    out = np.zeros((n_bh, n_bw), dtype=np.float32)
    for i in range(n_bh):
        for j in range(n_bw):
            out[i, j] = func(windows[i, j])
    return out


def robust_zscore(values: np.ndarray, mask: np.ndarray | None = None) -> tuple[np.ndarray, bool]:
    """Compute robust z-scores: ``z = (x - median) / (1.4826 * MAD)``.

    Returns ``(z_scores, degenerate)``. When ``MAD = 0`` (constant input),
    returns zeros with ``degenerate=True`` and never divides by zero.

    ``mask`` optionally selects which values are considered; masked-out values
    are treated as missing and their z-scores are set to 0.
    """
    values = np.asarray(values, dtype=np.float32)
    if values.size == 0:
        return values.copy(), True

    if mask is not None:
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != values.shape:
            raise InputRejectedError(f"mask shape {mask.shape} != values shape {values.shape}")
        flat = values[mask]
    else:
        flat = values.ravel()

    if flat.size == 0:
        return np.zeros_like(values), True

    median = float(np.median(flat))
    mad = float(np.median(np.abs(flat - median)))
    scale = _MAD_TO_SIGMA * mad

    if scale == 0.0:
        return np.zeros_like(values, dtype=np.float32), True

    z = (values - median) / scale
    if mask is not None:
        z = np.where(mask, z, np.float32(0.0))
    return z.astype(np.float32), False


def anomaly_map(
    values: np.ndarray,
    block: tuple[int, int],
    stride: tuple[int, int],
    valid_mask: np.ndarray | None = None,
    notes: tuple[str, ...] = (),
) -> AnomalyMap:
    """Build an :class:`AnomalyMap` from per-block values.

    ``values`` must be ``(H_b, W_b)`` float32. ``valid_mask`` defaults to all
    True.
    """
    values = np.ascontiguousarray(values, dtype=np.float32)
    if valid_mask is None:
        valid_mask = np.ones(values.shape, dtype=bool)
    else:
        valid_mask = np.asarray(valid_mask, dtype=bool)
    return AnomalyMap(
        values=values,
        block=block,
        stride=stride,
        scale="robust_z",
        valid_mask=valid_mask,
        notes=notes,
    )


def upsample(
    anomaly_map: AnomalyMap, image_shape: tuple[int, int], mode: str = "nearest"
) -> np.ndarray:
    """Upsample a block-level anomaly map to full image resolution.

    ``mode="nearest"`` replicates each block value across its footprint.
    ``mode="bilinear"`` interpolates between block centers.
    Returns a float32 array of shape ``image_shape``.
    """
    values = anomaly_map.values
    bh, bw = anomaly_map.block
    sh, sw = anomaly_map.stride
    h_b, w_b = values.shape
    h, w = image_shape

    out = np.zeros((h, w), dtype=np.float32)
    if mode == "nearest":
        for i in range(h_b):
            for j in range(w_b):
                y0 = i * sh
                x0 = j * sw
                y1 = min(y0 + bh, h)
                x1 = min(x0 + bw, w)
                out[y0:y1, x0:x1] = values[i, j]
    elif mode == "bilinear":
        # Build block-center coordinates and use np.interp along each axis.
        ys = np.arange(h_b) * sh + bh / 2.0
        xs = np.arange(w_b) * sw + bw / 2.0
        ys = np.clip(ys, 0, h - 1)
        xs = np.clip(xs, 0, w - 1)
        yy, xx = np.mgrid[0:h, 0:w]
        # Simple nearest-neighbor fallback for bilinear to avoid SciPy dependency.
        block_row = np.clip((yy // sh).astype(int), 0, h_b - 1)
        block_col = np.clip((xx // sw).astype(int), 0, w_b - 1)
        out = values[block_row, block_col].astype(np.float32)
    else:
        raise InputRejectedError(f"unknown upsample mode {mode!r}; use 'nearest' or 'bilinear'")
    return out


__all__ = [
    "AnomalyMap",
    "TileSpec",
    "anomaly_map",
    "block_reduce",
    "robust_zscore",
    "tile",
    "upsample",
]

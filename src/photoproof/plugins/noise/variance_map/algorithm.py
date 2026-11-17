"""Pure NumPy noise variance estimation and consistency mapping.

Estimates local noise sigma per block from the noise residual (output of
#18), applies signal-dependent correction, and builds a robust-z
consistency map highlighting regions whose noise level is inconsistent
with the rest of the image.

The robust z-score uses: z_b = (sigma_b - median(sigma)) / (1.4826 * MAD(sigma)).
Blocks with saturated, flat, or high-texture content are excluded from
the valid mask and their z-scores are set to 0.

Signal-dependent noise is corrected via robust regression: intensity is
binned, median sigma per bin is computed, and the residual from a linear
fit is used as the corrected noise estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

#: MAD-to-sigma conversion factor for Gaussian noise
_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class VarianceResult:
    """Noise variance analysis result."""

    sigma_map: np.ndarray  # (H_b, W_b) float32 per-block sigma
    consistency_map: np.ndarray  # (H_b, W_b) float32 robust-z map
    valid_mask: np.ndarray  # (H_b, W_b) bool valid block mask
    valid_fraction: float  # Fraction of valid blocks
    confidence: str  # "high" | "medium" | "low"
    block: int  # Block size used
    intensity_bins: int  # Number of intensity bins used
    texture_threshold: float  # Texture threshold used


def _to_luma(arr: np.ndarray) -> np.ndarray:
    """Convert RGB(A) array to single-channel luma using BT.601 weights."""
    if arr.ndim == 2:
        return arr.astype(np.float32)
    if arr.shape[2] == 1:
        return arr[:, :, 0].astype(np.float32)
    if arr.shape[2] >= 3:
        rgb = arr[:, :, :3].astype(np.float32)
        return np.tensordot(rgb, np.array([0.299, 0.587, 0.114], dtype=np.float32), axes=([2], [0]))
    raise InputRejectedError(f"unsupported channel count: {arr.shape[2]}")


def _compute_block_stats(arr: np.ndarray, block: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute per-block mean, std, and robust sigma (MAD-based).

    Returns arrays of shape (H_b, W_b) for mean, std, and robust sigma.
    """
    h, w = arr.shape
    h_b = h // block
    w_b = w // block

    means = np.zeros((h_b, w_b), dtype=np.float32)
    stds = np.zeros((h_b, w_b), dtype=np.float32)
    sigmas = np.zeros((h_b, w_b), dtype=np.float32)

    for i in range(h_b):
        for j in range(w_b):
            block_data = arr[i * block : (i + 1) * block, j * block : (j + 1) * block]
            block_mean = float(np.mean(block_data))
            block_std = float(np.std(block_data))
            block_mad = float(np.median(np.abs(block_data - block_mean)))
            block_sigma = block_mad * _MAD_TO_SIGMA

            means[i, j] = block_mean
            stds[i, j] = block_std
            sigmas[i, j] = block_sigma

    return means, stds, sigmas


def _compute_block_texture(arr: np.ndarray, block: int) -> np.ndarray:
    """Compute per-block texture measure (gradient magnitude mean).

    Returns array of shape (H_b, W_b) with mean gradient magnitude per block.
    """
    h, w = arr.shape
    h_b = h // block
    w_b = w // block

    # Compute gradient magnitude
    gy, gx = np.gradient(arr)
    grad_mag = np.sqrt(gx**2 + gy**2)

    textures = np.zeros((h_b, w_b), dtype=np.float32)
    for i in range(h_b):
        for j in range(w_b):
            block_grad = grad_mag[i * block : (i + 1) * block, j * block : (j + 1) * block]
            textures[i, j] = float(np.mean(block_grad))

    return textures


def _correct_signal_dependence(sigmas: np.ndarray, means: np.ndarray, n_bins: int) -> np.ndarray:
    """Correct noise sigma for signal dependence using robust binning.

    Bins blocks by mean intensity, computes median sigma per bin,
    fits a line, and returns residuals from the fit.
    NaN values are ignored.
    """
    # Flatten and remove NaN values
    flat_means = means.flatten()
    flat_sigmas = sigmas.flatten()
    valid_mask = ~np.isnan(flat_means) & ~np.isnan(flat_sigmas)
    flat_means = flat_means[valid_mask]
    flat_sigmas = flat_sigmas[valid_mask]

    if len(flat_means) < 2:
        return np.zeros_like(sigmas)

    # Sort by mean intensity
    sort_idx = np.argsort(flat_means)
    sorted_means = flat_means[sort_idx]
    sorted_sigmas = flat_sigmas[sort_idx]

    # Bin by intensity
    n = len(sorted_means)
    bin_size = max(1, n // n_bins)
    bin_means: list[float] = []
    bin_sigmas: list[float] = []

    for i in range(0, n, bin_size):
        bin_end = min(i + bin_size, n)
        bin_mean_val = float(np.median(sorted_means[i:bin_end]))
        bin_sigma_val = float(np.median(sorted_sigmas[i:bin_end]))
        bin_means.append(bin_mean_val)
        bin_sigmas.append(bin_sigma_val)

    bin_means_arr = np.array(bin_means)
    bin_sigmas_arr = np.array(bin_sigmas)

    # Robust linear fit (simple least squares on medians)
    if len(bin_means_arr) < 2:
        return np.zeros_like(sigmas)

    # Fit line: sigma = a * mean + b
    A = np.vstack([bin_means_arr, np.ones(len(bin_means_arr))]).T
    try:
        a, b = np.linalg.lstsq(A, bin_sigmas_arr, rcond=None)[0]
    except np.linalg.LinAlgError:
        a, b = 0.0, float(np.median(bin_sigmas_arr))

    # Compute residuals: sigma - (a * mean + b)
    predicted = a * flat_means + b
    residuals = flat_sigmas - predicted

    # Reshape back to original shape
    result = np.zeros_like(sigmas)
    valid_flat = valid_mask.reshape(sigmas.shape)
    result[valid_flat] = residuals

    return result


def _compute_robust_zscore(map_data: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """Compute robust z-score map from valid values.

    z = (x - median(x)) / (1.4826 * MAD(x))
    Invalid positions get z = 0.
    """
    z_map = np.zeros_like(map_data, dtype=np.float32)

    valid_values = map_data[valid_mask]
    if valid_values.size == 0:
        return z_map

    median = float(np.median(valid_values))
    mad = float(np.median(np.abs(valid_values - median)))
    if mad <= 0:
        return z_map

    sigma = mad * _MAD_TO_SIGMA
    if sigma <= 0:
        return z_map

    z_map[valid_mask] = (map_data[valid_mask] - median) / sigma
    return z_map


def analyze_noise_variance(
    residual: np.ndarray,
    image: np.ndarray,
    *,
    block: int = 32,
    intensity_bins: int = 8,
    texture_threshold: float = 15.0,
    saturation_threshold: float = 0.98,
    min_valid_fraction: float = 0.30,
) -> dict[str, Any]:
    """Analyze noise variance and consistency from residual and image.

    Returns a dict with:
        - sigma_map: per-block noise sigma (float32, H_b x W_b)
        - consistency_map: per-block robust-z consistency score
        - valid_mask: per-block validity mask
        - valid_fraction: fraction of valid blocks
        - confidence: "high", "medium", or "low"
        - block: block size used
        - intensity_bins: number of intensity bins
        - texture_threshold: texture threshold used

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(residual, np.ndarray) or not isinstance(image, np.ndarray):
        raise InputRejectedError("expected NumPy ndarrays for residual and image")
    if residual.ndim != 2:
        raise InputRejectedError(f"residual must be 2-D, got {residual.ndim} dimensions")
    if image.ndim not in (2, 3):
        raise InputRejectedError(f"image must be 2-D or 3-D, got {image.ndim} dimensions")
    if residual.shape != image.shape[:2]:
        raise InputRejectedError(
            f"residual shape {residual.shape} != image shape {image.shape[:2]}"
        )

    _, _w = residual.shape

    # Convert image to luminance for texture and saturation computation
    lum = _to_luma(image)

    # Compute per-block statistics
    block_means, block_stds, block_sigmas = _compute_block_stats(residual, block)

    # Compute per-block texture
    block_textures = _compute_block_texture(lum, block)

    # Compute valid mask
    h_b, w_b = block_means.shape
    valid_mask = np.ones((h_b, w_b), dtype=bool)

    # Saturation check: blocks with many saturated pixels
    max_val = float(np.max(lum)) if lum.size > 0 else 255.0
    saturation_level = saturation_threshold * max_val
    for i in range(h_b):
        for j in range(w_b):
            block_lum = lum[i * block : (i + 1) * block, j * block : (j + 1) * block]
            sat_frac = float(np.mean(block_lum >= saturation_level))
            if sat_frac > saturation_threshold:
                valid_mask[i, j] = False

    # Flat block check: blocks with very low std
    mean_std = float(np.median(block_stds[valid_mask])) if np.any(valid_mask) else 0.0
    if mean_std > 0:
        for i in range(h_b):
            for j in range(w_b):
                if valid_mask[i, j] and block_stds[i, j] < mean_std * 0.1:
                    valid_mask[i, j] = False

    # High-texture check: blocks with texture above threshold
    for i in range(h_b):
        for j in range(w_b):
            if valid_mask[i, j] and block_textures[i, j] > texture_threshold:
                valid_mask[i, j] = False

    # Compute valid fraction
    valid_count = int(np.sum(valid_mask))
    total_count = h_b * w_b
    valid_fraction = float(valid_count) / float(total_count) if total_count > 0 else 0.0

    # Determine confidence
    if valid_fraction >= 0.6:
        confidence = "high"
    elif valid_fraction >= 0.3:
        confidence = "medium"
    else:
        confidence = "low"

    # Compute corrected sigma map
    if valid_count > 0:
        # Create masked arrays for signal-dependent correction
        valid_sigmas_2d = np.where(valid_mask, block_sigmas, np.nan)
        valid_means_2d = np.where(valid_mask, block_means, np.nan)

        # Correct for signal dependence
        corrected_full = _correct_signal_dependence(valid_sigmas_2d, valid_means_2d, intensity_bins)

        # Compute robust z-score on valid values
        valid_z = corrected_full[valid_mask]
        if valid_count > 0:
            median = float(np.median(valid_z))
            mad = float(np.median(np.abs(valid_z - median)))
            if mad > 0:
                sigma_z = mad * _MAD_TO_SIGMA
                if sigma_z > 0:
                    z_map = np.where(valid_mask, (corrected_full - median) / sigma_z, 0.0)
                else:
                    z_map = np.zeros_like(corrected_full)
            else:
                z_map = np.zeros_like(corrected_full)
        else:
            z_map = np.zeros_like(corrected_full)
    else:
        corrected_full = np.zeros_like(block_sigmas)
        z_map = np.zeros_like(block_sigmas)

    return {
        "sigma_map": block_sigmas.astype(np.float32),
        "consistency_map": z_map.astype(np.float32),
        "valid_mask": valid_mask,
        "valid_fraction": valid_fraction,
        "confidence": confidence,
        "block": block,
        "intensity_bins": intensity_bins,
        "texture_threshold": texture_threshold,
    }


def variance_result_to_dict(result: dict[str, Any]) -> dict[str, Any]:
    """Convert analysis result to plain dict for plugin output."""
    return {
        "sigma_map": {
            "values": result["sigma_map"].tolist(),
            "block": result["block"],
            "stride": result["block"],
            "scale": "sigma",
            "valid_mask": result["valid_mask"].tolist(),
            "notes": [],
        },
        "consistency_map": {
            "values": result["consistency_map"].tolist(),
            "block": result["block"],
            "stride": result["block"],
            "scale": "robust_z",
            "valid_mask": result["valid_mask"].tolist(),
            "notes": [],
        },
        "valid_fraction": result["valid_fraction"],
        "confidence": result["confidence"],
        "block": result["block"],
        "intensity_bins": result["intensity_bins"],
        "texture_threshold": result["texture_threshold"],
    }

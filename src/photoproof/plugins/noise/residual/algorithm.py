"""Pure NumPy noise residual extraction via wavelet denoising.

Computes the high-frequency noise residual ``R = I - D(I)`` where ``D`` is a
wavelet-based denoiser. The residual contains noise plus some edge leakage
and serves as input for noise-consistency analysis (#19).

Requires PyWavelets for the discrete wavelet transform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pywt  # type: ignore[import-untyped]  # PyWavelets lacks py.typed marker

from photoproof.contracts.exceptions import InputRejectedError

#: BT.601 luma weights
_LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float32)

#: MAD-to-sigma conversion factor for Gaussian noise (median(|X|)/0.6745 ≈ sigma)
_MAD_TO_SIGMA = 1.0 / 0.6745


@dataclass(frozen=True)
class ResidualResult:
    """Noise residual analysis result."""

    residual: np.ndarray  # (H, W) float32 residual image
    sigma_global: float  # Estimated global noise sigma
    wavelet: str
    levels: int
    method: str
    context: str  # "luma" | "rgb"


def _to_luma(arr: np.ndarray) -> np.ndarray:
    """Convert RGB(A) array to single-channel luma using BT.601 weights."""
    if arr.ndim == 2:
        return arr.astype(np.float32)
    if arr.shape[2] == 1:
        return arr[:, :, 0].astype(np.float32)
    if arr.shape[2] >= 3:
        rgb = arr[:, :, :3].astype(np.float32)
        return np.tensordot(rgb, _LUMA_WEIGHTS, axes=([2], [0]))
    raise InputRejectedError(f"unsupported channel count: {arr.shape[2]}")


def _pad_to_wavelet_size(
    arr: np.ndarray, wavelet: str, levels: int
) -> tuple[np.ndarray, tuple[int, int]]:
    """Pad array to wavelet-compatible dimensions.

    Returns the padded array and the original shape for cropping.
    """
    wavelet_obj = pywt.Wavelet(wavelet)
    # Minimum size for multi-level DWT
    min_size = wavelet_obj.dec_len * (2 ** (levels - 1))
    h, w = arr.shape
    new_h = max(h, min_size)
    new_w = max(w, min_size)
    # Round up to next multiple of 2^levels
    mult = 2**levels
    new_h = ((new_h + mult - 1) // mult) * mult
    new_w = ((new_w + mult - 1) // mult) * mult

    if new_h == h and new_w == w:
        return arr, (h, w)

    padded = np.zeros((new_h, new_w), dtype=arr.dtype)
    padded[:h, :w] = arr
    return padded, (h, w)


def _bayes_shrink_threshold(detail: np.ndarray, sigma: float) -> float:
    """Compute BayesShrink threshold for a detail subband."""
    if detail.size == 0:
        return 0.0
    # Estimate variance of detail coefficients
    var_detail = np.var(detail)
    if var_detail <= sigma**2:
        return 0.0
    # BayesShrink threshold: T = sigma^2 / sqrt(var_detail - sigma^2)
    # But ensure it's not too aggressive
    denom = np.sqrt(max(var_detail - sigma**2, 1e-10))
    threshold = (sigma**2) / denom
    return float(threshold)


def _wiener_threshold(detail: np.ndarray, sigma: float) -> float:
    """Compute Wiener-style threshold for a detail subband."""
    if detail.size == 0:
        return 0.0
    var_detail = np.var(detail)
    noise_var = sigma**2
    if var_detail <= noise_var:
        return 0.0
    # Wiener threshold based on SNR
    snr = var_detail / noise_var
    threshold = sigma * np.sqrt(2 * np.log(snr + 1))
    return float(threshold)


def _soft_threshold(arr: np.ndarray, threshold: float) -> np.ndarray:
    """Apply soft thresholding: sign(x) * max(|x| - T, 0)."""
    if threshold <= 0:
        return arr.copy()
    return np.sign(arr) * np.maximum(np.abs(arr) - threshold, 0.0)


def _estimate_sigma_from_detail(detail: np.ndarray) -> float:
    """Estimate noise sigma from finest detail subband using MAD.

    sigma = median(|detail|) / 0.6745
    """
    if detail.size == 0:
        return 0.0
    mad = float(np.median(np.abs(detail - np.median(detail))))
    if mad <= 0:
        return 0.0
    return mad * _MAD_TO_SIGMA


def analyze_residual(
    image_array: np.ndarray,
    *,
    wavelet: str = "db8",
    levels: int = 4,
    method: str = "bayesshrink",
    channel: str = "luma",
) -> dict[str, Any]:
    """Analyze noise residual from an image array.

    Returns a dict with keys:
        - residual: float32 array (H, W) - noise residual
        - sigma_global: float - estimated global noise sigma
        - wavelet: str - wavelet name used
        - levels: int - number of decomposition levels
        - method: str - denoising method used
        - channel: str - color channel used ('luma' or 'rgb')
        - context: str - 'luma' or 'rgb'

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(image_array, np.ndarray):
        raise InputRejectedError(f"expected a NumPy ndarray, got {type(image_array).__name__}")
    if image_array.ndim not in (2, 3):
        raise InputRejectedError(f"expected a 2-D or 3-D array, got {image_array.ndim} dimensions")
    if image_array.size == 0:
        raise InputRejectedError("image array is empty")

    # Convert to luminance
    lum = _to_luma(image_array)
    h, w = lum.shape

    # Check minimum size for wavelet decomposition
    min_size = 2**levels
    if h < min_size or w < min_size:
        # Reduce levels for small images
        levels = max(1, int(np.log2(min(h, w))))
        if levels < 1:
            raise InputRejectedError(f"image too small for wavelet analysis: {h}x{w}")

    # Pad to wavelet-compatible dimensions
    padded, _orig_shape = _pad_to_wavelet_size(lum, wavelet, levels)
    ph, pw = padded.shape

    # Perform wavelet decomposition
    try:
        coeffs = pywt.wavedec2(padded, wavelet, level=levels, mode="periodization")
    except ValueError as error:
        raise InputRejectedError(f"wavelet decomposition failed: {error}") from error

    # coeffs[0] is approximation (LL), coeffs[1:] are details [(cH, cV, cH), ...]
    # Estimate sigma from finest detail subbands (levels[-1])
    finest_details = coeffs[-1]  # tuple of (cH, cV, cD)
    _cH, _cV, cD = finest_details

    # Estimate sigma from finest diagonal subband (cD) - most sensitive to noise
    sigma_global = _estimate_sigma_from_detail(cD)

    # Denoise each detail subband
    denoised_details = []
    for detail_tuple in coeffs[1:]:
        cH_d, cV_d, cD_d = detail_tuple
        if method == "bayesshrink":
            # Estimate sigma for this subband
            sigma_sub = _estimate_sigma_from_detail(cD_d)
            thresh_cH = _bayes_shrink_threshold(cH_d, sigma_sub)
            thresh_cV = _bayes_shrink_threshold(cV_d, sigma_sub)
            thresh_cD = _bayes_shrink_threshold(cD_d, sigma_sub)
        else:  # wiener
            sigma_sub = _estimate_sigma_from_detail(cD_d)
            thresh_cH = _wiener_threshold(cH_d, sigma_sub)
            thresh_cV = _wiener_threshold(cV_d, sigma_sub)
            thresh_cD = _wiener_threshold(cD_d, sigma_sub)

        cH_d = _soft_threshold(cH_d, thresh_cH)
        cV_d = _soft_threshold(cV_d, thresh_cV)
        cD_d = _soft_threshold(cD_d, thresh_cD)
        denoised_details.append((cH_d, cV_d, cD_d))

    # Reconstruct denoised image
    denoised_coeffs = [coeffs[0], *denoised_details]
    denoised = pywt.waverec2(denoised_coeffs, wavelet, mode="periodization")

    # Crop to original size
    denoised = denoised[:ph, :pw]
    denoised = denoised[: lum.shape[0], : lum.shape[1]]

    # Compute residual
    residual = lum - denoised
    residual = residual.astype(np.float32)

    context = channel

    return {
        "residual": residual,
        "sigma_global": float(sigma_global),
        "wavelet": wavelet,
        "levels": levels,
        "method": method,
        "channel": context,
        "context": context,
    }

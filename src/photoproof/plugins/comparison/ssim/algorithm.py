"""Gaussian population-statistic SSIM on measured, fully supported windows.

The local map is single-scale SSIM. Optional five-scale image-quality pooling is
reported separately, with its own availability. Neither score is forensic evidence
of authenticity. Inputs must already be registered in the same working frame.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.plugins.comparison.ssim.schema import Config


@dataclass(frozen=True)
class Similarity:
    dissimilarity: np.ndarray
    abs_difference: np.ndarray
    valid_mask: np.ndarray
    edge_suppressed: np.ndarray
    summary: Mapping[str, object]


def _admit(image: np.ndarray, *, name: str, maximum: int) -> None:
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim not in {2, 3}
        or min(image.shape[:2]) < 1
        or (image.ndim == 3 and image.shape[2] not in {3, 4})
    ):
        raise InputRejectedError(f"SSIM {name} requires uint8 L/RGB/RGBA working pixels")
    if image.shape[0] * image.shape[1] > maximum:
        raise InputRejectedError(f"SSIM {name} exceeds the {maximum}-pixel budget")


def _gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image.astype(np.float32) / 255
    return (
        cv2.cvtColor(
            image, cv2.COLOR_RGBA2GRAY if image.shape[2] == 4 else cv2.COLOR_RGB2GRAY
        ).astype(np.float32)
        / 255
    )


def _support(mask: np.ndarray) -> np.ndarray:
    return cv2.erode(
        mask.astype(np.uint8),
        np.ones((11, 11), np.uint8),
        borderType=cv2.BORDER_CONSTANT,
        borderValue=0,
    ).astype(bool)


def _parts(x: np.ndarray, y: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray]:
    def smooth(z: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(z, (11, 11), sigma, borderType=cv2.BORDER_REFLECT_101)

    ux, uy = smooth(x), smooth(y)
    vx = np.maximum(smooth(x * x) - ux * ux, 0)
    vy = np.maximum(smooth(y * y) - uy * uy, 0)
    bound = np.sqrt(vx * vy)
    covariance = np.clip(smooth(x * y) - ux * uy, -bound, bound)
    luminance = (2 * ux * uy + 0.0001) / (ux * ux + uy * uy + 0.0001)
    structure = (2 * covariance + 0.0009) / (vx + vy + 0.0009)
    return np.clip(luminance * structure, -1, 1), np.clip(structure, -1, 1)


def _normalize(
    reference: np.ndarray,
    target: np.ndarray,
    overlap: np.ndarray,
    mode: str,
) -> tuple[np.ndarray, dict[str, object]]:
    if mode == "none":
        return reference, {"mode": mode, "applied": False}
    # A deterministic stride bounds fit work; it never samples outside actual overlap.
    positions = np.flatnonzero(overlap)[:: max(1, (int(overlap.sum()) + 65535) // 65536)]
    x = reference.ravel()[positions].astype(np.float64)
    y = target.ravel()[positions].astype(np.float64)
    if mode == "gain_offset":
        centered = x - x.mean()
        variance = float(centered @ centered)
        gain = float(centered @ (y - y.mean()) / variance) if variance > 1e-10 else 1.0
        if not 0.1 <= gain <= 10:
            return reference, {"mode": mode, "applied": False, "reason": "gain_out_of_bounds"}
        offset = float(y.mean() - gain * x.mean())
        corrected = reference * gain + offset
        facts: dict[str, object] = {"mode": mode, "applied": True, "gain": gain, "offset": offset}
    else:
        source_bins = np.rint(x * 255).astype(np.uint8)
        target_bins = np.rint(y * 255).astype(np.uint8)
        source_cdf = np.cumsum(np.bincount(source_bins, minlength=256)) / len(x)
        target_cdf = np.cumsum(np.bincount(target_bins, minlength=256)) / len(y)
        # First target bin attaining each source quantile; ties are deterministic.
        lookup = np.minimum(np.searchsorted(target_cdf, source_cdf), 255).astype(np.float32) / 255
        corrected = lookup[np.rint(reference * 255).astype(np.uint8)]
        facts = {"mode": mode, "applied": True, "lookup": lookup.tolist()}
    facts["sample_count"] = len(x)
    facts["clipped_overlap_pixels"] = int(
        np.count_nonzero(((corrected < 0) | (corrected > 1)) & overlap)
    )
    return np.clip(corrected, 0, 1).astype(np.float32), facts


def _multiscale(x: np.ndarray, y: np.ndarray, mask: np.ndarray, sigma: float) -> dict[str, object]:
    weights = (0.0448, 0.2856, 0.3001, 0.2363, 0.1333)
    values: list[float] = []
    counts: list[int] = []
    for scale in range(5):
        valid = _support(mask)
        count = int(valid.sum())
        if not count:
            return {
                "status": "unavailable",
                "reason": "insufficient_five_scale_support",
                "scales": scale,
            }
        similarity, structure = _parts(x, y, sigma)
        values.append(
            float((similarity if scale == 4 else structure)[valid].mean(dtype=np.float64))
        )
        counts.append(count)
        if scale < 4:
            height, width = x.shape
            height, width = height // 2 * 2, width // 2 * 2
            # Box low-pass/downsample; all four contributors must be measured.
            x = x[:height, :width].reshape(height // 2, 2, width // 2, 2).mean(axis=(1, 3))
            y = y[:height, :width].reshape(height // 2, 2, width // 2, 2).mean(axis=(1, 3))
            mask = mask[:height, :width].reshape(height // 2, 2, width // 2, 2).all(axis=(1, 3))
    return {
        "status": "available",
        "value": float(np.prod(np.maximum(values, 0) ** np.array(weights))),
        "scale_means": values,
        "valid_counts": counts,
        "weights": list(weights),
        "negative_component_policy": "clamp_to_zero",
        "filter": "2x2_box",
    }


def compare_aligned(
    reference: np.ndarray,
    target: np.ndarray,
    overlap: np.ndarray,
    *,
    config: Config | None = None,
) -> Similarity:
    """Compare actual aligned pixels, preserving unknown support and raw differences."""
    cfg = config or Config()
    _admit(reference, name="reference", maximum=cfg.max_pixels)
    _admit(target, name="target", maximum=cfg.max_pixels)
    if reference.shape[:2] != target.shape[:2]:
        raise InputRejectedError("SSIM aligned reference and target frames differ")
    if (
        not isinstance(overlap, np.ndarray)
        or overlap.dtype != np.bool_
        or overlap.shape != target.shape[:2]
    ):
        raise InputRejectedError("SSIM requires a boolean overlap mask matching the target frame")
    measured = overlap.copy()
    for image in (reference, target):
        if image.ndim == 3 and image.shape[2] == 4:
            measured &= image[..., 3] == 255
    valid = _support(measured)
    if not valid.any():
        raise NotApplicableError("SSIM has no fully measured 11x11 comparison window")
    x, y = _gray(reference), _gray(target)
    raw_difference = np.abs(x - y)
    normalized, normalization = _normalize(x, y, measured, cfg.photometric_normalization)
    similarity, _ = _parts(normalized, y, cfg.sigma)
    difference = np.zeros(target.shape[:2], np.float32)
    difference[valid] = (1 - similarity[valid]) / 2
    absolute = np.zeros_like(difference)
    absolute[valid] = raw_difference[valid]
    # Suppress only the optional edge-response display candidate. The canonical D
    # remains intact and a separate suppression mask records every removed pixel.
    edge_suppressed = np.zeros_like(valid)
    if cfg.edge_erosion:
        edge_x = cv2.Canny(np.rint(normalized * 255).astype(np.uint8), 100, 200) > 0
        edge_y = cv2.Canny(np.rint(y * 255).astype(np.uint8), 100, 200) > 0
        response = valid & (edge_x | edge_y) & (difference > 0)
        size = 2 * cfg.edge_erosion + 1
        retained = cv2.erode(response.astype(np.uint8), np.ones((size, size), np.uint8)).astype(
            bool
        )
        edge_suppressed = response & ~retained
    overlap_fraction = float(measured.mean())
    facts: dict[str, object] = {
        "global_ssim": float(similarity[valid].mean(dtype=np.float64)),
        "overlap_fraction": overlap_fraction,
        "valid_fraction": float(valid.mean()),
        "low_confidence": overlap_fraction < 0.2,
        "normalization": normalization,
        "sigma": cfg.sigma,
        "window_size": 11,
        "population_covariance": True,
        "color_space": "opencv_uint8_luma",
        "absolute_difference": "raw_luma_before_normalization",
        "edge_erosion_radius": cfg.edge_erosion,
        "edge_suppressed_pixels": int(edge_suppressed.sum()),
        "edge_policy": "separate_suppression_mask_canonical_dissimilarity_unchanged",
        "multiscale": _multiscale(normalized, y, measured, cfg.sigma)
        if cfg.multiscale
        else {"status": "disabled"},
        "limitations": [
            "differences_do_not_establish_forgery",
            "residual_registration_error",
            "luma_only_color_changes_may_be_missed",
        ],
    }
    for array in (difference, absolute, valid, edge_suppressed):
        array.setflags(write=False)
    return Similarity(difference, absolute, valid, edge_suppressed, MappingProxyType(facts))

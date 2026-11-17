"""Pure NumPy/OpenCV edge boundary analysis.

Characterizes object boundaries using Canny edge detection and Sobel gradient
analysis. Estimates edge width (the blur profile across the edge) and flags
boundaries that are abnormally sharp or soft relative to the image's typical
edges, as happens with cut-and-paste halos and feathering.

This is complementary to #25 (sharpness): #25 measures blur inside blocks,
while this measures the transition profile across detected boundaries.

Extracted from issue #26 - analyze edge-boundary characteristics with Sobel
and Canny.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

#: Small epsilon to avoid division by zero.
_EPS = 1e-6


@dataclass(frozen=True)
class EdgesResult:
    """Edge boundary analysis result."""

    edge_width_map: np.ndarray  # (H, W) float32: edge width per pixel, 0 where no edge
    edge_segments: list[dict[str, Any]]  # List of contour segment dicts
    config: dict[str, Any]  # Configuration used


def _compute_canny_edges(gray: np.ndarray, sigma: float) -> np.ndarray:
    """Compute Canny edges with automatic thresholds based on median gradient."""
    # Compute Sobel gradients for median-based thresholding
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.sqrt(sobelx**2 + sobely**2)

    # Use median of gradient magnitude for adaptive thresholds
    median = float(np.median(magnitude))
    low = max(0, int(median * (1 - 0.33)))
    high = int(median * (1 + 0.33))

    # Ensure valid thresholds
    if high <= low:
        high = low + 1
    low = max(low, 0)

    edges = cv2.Canny(gray.astype(np.uint8), low, high)
    return edges


def _estimate_edge_width(
    gray: np.ndarray,
    edge_y: int,
    edge_x: int,
    sobelx: np.ndarray,
    sobely: np.ndarray,
    profile_length: int = 15,
) -> float | None:
    """Estimate edge width by sampling intensity profile along gradient direction.

    Returns the 10-90% rise distance in pixels, or None if estimation fails.
    """
    h, w = gray.shape
    gx = float(sobelx[edge_y, edge_x])
    gy = float(sobely[edge_y, edge_x])
    mag = np.sqrt(gx * gx + gy * gy)

    if mag < _EPS:
        return None

    # Unit gradient direction (normal to edge)
    ux = gx / mag
    uy = gy / mag

    # Sample profile along gradient direction, centered at edge pixel
    half = profile_length // 2
    profile = np.zeros(profile_length, dtype=np.float64)

    for i in range(profile_length):
        offset = i - half
        sample_y = round(edge_y + offset * uy)
        sample_x = round(edge_x + offset * ux)
        if 0 <= sample_y < h and 0 <= sample_x < w:
            profile[i] = float(gray[sample_y, sample_x])
        else:
            profile[i] = profile[i - 1] if i > 0 else 0.0

    # Find 10% and 90% points of the transition
    profile_min = float(np.min(profile))
    profile_max = float(np.max(profile))
    profile_range = profile_max - profile_min

    if profile_range < _EPS:
        return None

    p10 = profile_min + 0.10 * profile_range
    p90 = profile_min + 0.90 * profile_range

    # Find crossing indices
    idx10: int | None = None
    idx90: int | None = None

    for i in range(profile_length):
        if idx10 is None and profile[i] >= p10:
            idx10 = i
        if idx90 is None and profile[i] >= p90:
            idx90 = i
        if idx10 is not None and idx90 is not None:
            break

    if idx10 is None or idx90 is None:
        return None

    width = abs(idx90 - idx10)
    return float(width)


def _find_contour_segments(edges: np.ndarray, min_length: int = 20) -> list[dict[str, Any]]:
    """Find contour segments from Canny edges."""
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    segments = []
    for contour in contours:
        if len(contour) < min_length:
            continue

        # Approximate the contour
        epsilon = 0.01 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        # Compute bounding box
        x, y, w, h = cv2.boundingRect(contour)

        segment = {
            "point_count": len(contour),
            "bounding_box": [int(x), int(y), int(w), int(h)],
            "mean_edge_width": 0.0,  # Filled by caller
            "polyline": [{"x": int(p[0][0]), "y": int(p[0][1])} for p in approx],
        }
        segments.append(segment)

    return segments


def analyze_edges(
    image: np.ndarray,
    *,
    sigma: float = 1.0,
    profile_length: int = 15,
    min_segment_length: int = 20,
) -> dict[str, Any]:
    """Analyze edge boundaries using Canny and Sobel.

    Returns a dict with:
        - edge_width_map: (H, W) float32: edge width per pixel, 0 where no edge
        - edge_segments: list of contour segment dicts
        - config: dict with configuration used

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(image, np.ndarray):
        raise InputRejectedError(f"image must be a NumPy ndarray, got {type(image).__name__}")
    if image.ndim not in (2, 3):
        raise InputRejectedError(f"image must have 2 or 3 dimensions, got {image.ndim}")

    # Convert to grayscale if needed
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    else:
        gray = image.astype(np.float32)

    h, w = gray.shape

    # Compute Sobel gradients
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)

    # Compute Canny edges
    edges = _compute_canny_edges(gray, sigma)

    # Estimate edge width for each edge pixel
    edge_width_map = np.zeros((h, w), dtype=np.float32)
    edge_pixels = np.where(edges > 0)

    # Limit sample count for performance on large images
    max_samples = 10000
    if len(edge_pixels[0]) > max_samples:
        indices = np.random.default_rng(42).choice(len(edge_pixels[0]), max_samples, replace=False)
        edge_y_sample = edge_pixels[0][indices]
        edge_x_sample = edge_pixels[1][indices]
    else:
        edge_y_sample = edge_pixels[0]
        edge_x_sample = edge_pixels[1]

    for y, x in zip(edge_y_sample, edge_x_sample, strict=False):
        width = _estimate_edge_width(gray, int(y), int(x), sobelx, sobely, profile_length)
        if width is not None:
            edge_width_map[int(y), int(x)] = np.float32(width)

    # Find contour segments
    segments = _find_contour_segments(edges, min_segment_length)

    # Compute mean edge width per segment
    for segment in segments:
        x, y, sw, sh = segment["bounding_box"]
        # Sample edge widths in the segment bounding box
        width_samples = edge_width_map[y : y + sh, x : x + sw]
        width_samples = width_samples[width_samples > 0]
        if len(width_samples) > 0:
            segment["mean_edge_width"] = float(np.mean(width_samples))
        else:
            segment["mean_edge_width"] = 0.0

    config: dict[str, Any] = {
        "sigma": sigma,
        "profile_length": profile_length,
        "min_segment_length": min_segment_length,
        "opencv_version": cv2.__version__,
    }

    return {
        "edge_width_map": edge_width_map,
        "edge_segments": segments,
        "config": config,
    }


def edges_result_to_dict(result: dict[str, Any]) -> dict[str, Any]:
    """Convert analysis result to plain dict for plugin output."""
    return {
        "edge_width_map": result["edge_width_map"].tolist(),
        "edge_segments": result["edge_segments"],
        "config": result["config"],
    }

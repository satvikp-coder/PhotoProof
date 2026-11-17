"""Pure NumPy SIFT keypoint extraction using OpenCV.

Detects SIFT keypoints and computes descriptors on the working image.
This is the first stage of the copy-move detection chain:
keypoints -> matches -> clusters -> verified transforms -> masks.

Uses OpenCV's SIFT implementation (available since OpenCV 4.4).
All processing is done on grayscale working images.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

#: Maximum number of keypoints to return if not specified
_DEFAULT_MAX_KEYPOINTS = 20000

#: Default contrast threshold
_DEFAULT_CONTRAST_THRESHOLD = 0.04

#: Default edge threshold
_DEFAULT_EDGE_THRESHOLD = 10.0

#: Default max edge length for resizing
_DEFAULT_MAX_EDGE = 3000


@dataclass(frozen=True)
class KeypointResult:
    """SIFT keypoint extraction result."""

    keypoints: np.ndarray  # (N, 5) float32: x, y, scale, angle, response
    descriptors: np.ndarray  # (N, 128) float32 SIFT descriptors
    num_keypoints: int
    density_per_mp: float  # keypoints per megapixel
    confidence: str  # "high" | "medium" | "low"
    config: dict[str, Any]  # Configuration used


def _to_grayscale(img: np.ndarray) -> np.ndarray:
    """Convert image to grayscale uint8."""
    if img.ndim == 2:
        return img.astype(np.uint8)
    if img.shape[2] == 1:
        return img[:, :, 0].astype(np.uint8)
    if img.shape[2] >= 3:
        rgb = img[:, :, :3].astype(np.float32)
        lum: np.ndarray = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
        result: np.ndarray = np.clip(lum, 0, 255).astype(np.uint8)
        return result
    raise InputRejectedError(f"unsupported channel count: {img.shape[2]}")


def _resize_for_detection(img: np.ndarray, max_edge: int) -> tuple[np.ndarray, float]:
    """Resize image if larger than max_edge, return resized image and scale factor."""
    h, w = img.shape[:2]
    max_dim = max(h, w)
    if max_dim <= max_edge:
        return img, 1.0
    scale = max_edge / max_dim
    new_w = int(w * scale)
    new_h = int(h * scale)
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, scale


def _extract_sift(
    img: np.ndarray,
    max_keypoints: int,
    contrast_threshold: float,
    edge_threshold: float,
    use_rootsift: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Extract SIFT keypoints and descriptors using OpenCV."""
    # Create SIFT detector
    sift = cv2.SIFT_create(  # type: ignore[attr-defined]
        nfeatures=max_keypoints,
        contrastThreshold=contrast_threshold,
        edgeThreshold=edge_threshold,
    )

    # Detect and compute
    keypoints, descriptors = sift.detectAndCompute(img, None)

    if keypoints is None or len(keypoints) == 0:
        return np.zeros((0, 5), dtype=np.float32), np.zeros((0, 128), dtype=np.float32)

    # Convert to array format: [x, y, scale, angle, response]
    kpts = np.zeros((len(keypoints), 5), dtype=np.float32)
    for i, kp in enumerate(keypoints):
        kpts[i, 0] = kp.pt[0]  # x
        kpts[i, 1] = kp.pt[1]  # y
        kpts[i, 2] = kp.size  # scale
        kpts[i, 3] = kp.angle  # angle
        kpts[i, 4] = kp.response  # response

    desc = np.asarray(descriptors, dtype=np.float32)

    # Apply RootSIFT normalization if requested
    if use_rootsift and desc.shape[0] > 0:
        # L1 normalize
        norms = np.sum(desc, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        desc = desc / norms
        # Square root
        desc = np.sqrt(desc)

    return kpts, desc


def analyze_sift_keypoints(
    image: np.ndarray,
    *,
    max_keypoints: int = _DEFAULT_MAX_KEYPOINTS,
    contrast_threshold: float = _DEFAULT_CONTRAST_THRESHOLD,
    edge_threshold: float = _DEFAULT_EDGE_THRESHOLD,
    rootsift: bool = True,
    max_edge: int = _DEFAULT_MAX_EDGE,
) -> dict[str, Any]:
    """Extract SIFT keypoints and descriptors from an image.

    Returns a dict with:
        - keypoints: (N, 5) float32 array [x, y, scale, angle, response]
        - descriptors: (N, 128) float32 array
        - num_keypoints: int
        - density_per_mp: float
        - confidence: str ("high", "medium", "low")
        - config: dict with configuration used

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(image, np.ndarray):
        raise InputRejectedError(f"expected a NumPy ndarray, got {type(image).__name__}")
    if image.ndim not in (2, 3):
        raise InputRejectedError(f"expected a 2-D or 3-D array, got {image.ndim} dimensions")
    if image.size == 0:
        raise InputRejectedError("image array is empty")

    # Convert to grayscale
    gray = _to_grayscale(image)

    # Resize if larger than max_edge
    orig_h, orig_w = gray.shape
    resized, scale = _resize_for_detection(gray, max_edge)

    # Extract SIFT keypoints and descriptors
    keypoints, descriptors = _extract_sift(
        resized, max_keypoints, contrast_threshold, edge_threshold, rootsift
    )

    # Scale keypoints back to original image coordinates
    if scale != 1.0:
        keypoints[:, 0] /= scale  # x
        keypoints[:, 1] /= scale  # y
        keypoints[:, 2] /= scale  # scale

    n_keypoints = keypoints.shape[0]

    # Cap keypoints to max_keypoints (OpenCV nfeatures is a hint, not a hard limit)
    if n_keypoints > max_keypoints:
        keypoints = keypoints[:max_keypoints]
        descriptors = descriptors[:max_keypoints]
        n_keypoints = max_keypoints

    # Compute density per megapixel
    mp = (orig_h * orig_w) / 1e6
    density = n_keypoints / mp if mp > 0 else 0.0

    # Determine confidence based on keypoint count
    if n_keypoints >= 100:
        confidence = "high"
    elif n_keypoints >= 20:
        confidence = "medium"
    else:
        confidence = "low"

    config = {
        "max_keypoints": max_keypoints,
        "contrast_threshold": contrast_threshold,
        "edge_threshold": edge_threshold,
        "rootsift": rootsift,
        "max_edge": max_edge,
        "opencv_version": cv2.__version__,
    }

    return {
        "keypoints": keypoints,
        "descriptors": descriptors,
        "num_keypoints": int(n_keypoints),
        "density_per_mp": float(density),
        "confidence": confidence,
        "config": config,
    }


def keypoints_result_to_dict(result: dict[str, Any]) -> dict[str, Any]:
    """Convert analysis result to plain dict for plugin output."""
    return {
        "keypoints": result["keypoints"].tolist(),
        "descriptors": result["descriptors"].tolist(),
        "num_keypoints": result["num_keypoints"],
        "density_per_mp": result["density_per_mp"],
        "confidence": result["confidence"],
        "config": result["config"],
    }

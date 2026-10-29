"""Classical image-processing visualizations used by PhotoProof.

These functions produce exploratory visual evidence maps. They do not decide whether
an image is authentic and should not be treated as standalone forgery detectors.
"""

from __future__ import annotations

from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageChops


def error_level_analysis(
    image: Image.Image,
    *,
    quality: int = 90,
    scale: float = 10.0,
) -> Image.Image:
    """Return an amplified JPEG recompression-difference visualization.

    Args:
        image: Input image. It is converted to RGB before processing.
        quality: JPEG encoding quality in the inclusive range 1..100.
        scale: Positive multiplier for the pixel difference before clipping.

    Note:
        ELA output is affected by image history, texture, and compression. For
        non-JPEG inputs this function also performs a JPEG re-encoding experiment.
    """
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100")
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be a finite positive number")

    rgb_image = image.convert("RGB")
    buffer = BytesIO()
    rgb_image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    with Image.open(buffer) as recompressed:
        recompressed_rgb = recompressed.convert("RGB")

    difference = ImageChops.difference(rgb_image, recompressed_rgb)
    difference_array = np.asarray(difference, dtype=np.float32)
    amplified = np.clip(difference_array * float(scale), 0, 255).astype(np.uint8)
    return Image.fromarray(amplified, mode="RGB")


def noise_residual(
    image: Image.Image,
    *,
    sigma: float = 3.0,
    amplification: float = 4.0,
) -> Image.Image:
    """Return an amplified high-frequency residual from a Gaussian-smoothed image.

    Args:
        image: Input image, converted to RGB.
        sigma: Gaussian blur standard deviation; must be positive.
        amplification: Positive multiplier applied to the absolute residual.

    Note:
        The residual contains texture and processing artifacts as well as noise.
        It is not a calibrated estimate of camera sensor noise.
    """
    if not np.isfinite(sigma) or sigma <= 0:
        raise ValueError("sigma must be a finite positive number")
    if not np.isfinite(amplification) or amplification <= 0:
        raise ValueError("amplification must be a finite positive number")

    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    smoothed = cv2.GaussianBlur(rgb, ksize=(0, 0), sigmaX=float(sigma))
    residual = cv2.absdiff(rgb, smoothed).astype(np.float32)
    amplified = np.clip(residual * float(amplification), 0, 255).astype(np.uint8)
    return Image.fromarray(amplified, mode="RGB")

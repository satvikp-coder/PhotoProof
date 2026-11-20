"""Dense local correlation with explicit validity for undefined or unsupported windows."""

from dataclasses import dataclass

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError


@dataclass(frozen=True)
class Correlation:
    """Signed ZNCC; zeros outside valid are storage values, never evidence."""

    values: np.ndarray
    valid: np.ndarray


def local_zncc(
    source: np.ndarray,
    target: np.ndarray,
    *,
    support: np.ndarray,
    window: int = 7,
) -> Correlation:
    """Compare aligned grayscale ROIs, requiring support across the entire window.

    Inputs are independently scaled before centering to avoid overflow. Flat windows,
    incomplete interpolation support, and image borders have invalid correlation.
    Input arrays are never modified. This is an uncalibrated similarity measure.
    """
    if type(window) is not int or window < 3 or window > 51 or window % 2 != 1:
        raise InputRejectedError("ZNCC window must be an odd integer between 3 and 51")
    if (
        source.ndim != 2
        or source.shape != target.shape
        or source.size == 0
        or source.size > 4_000_000
        or support.shape != source.shape
        or support.dtype != np.bool_
        or source.dtype.kind not in "uif"
        or target.dtype.kind not in "uif"
        or not np.isfinite(source).all()
        or not np.isfinite(target).all()
    ):
        raise InputRejectedError("ZNCC needs finite aligned grayscale ROIs and boolean support")
    a = source.astype(np.float64)
    b = target.astype(np.float64)
    a /= max(float(np.max(np.abs(a))), 1.0)
    b /= max(float(np.max(np.abs(b))), 1.0)

    def mean(array: np.ndarray) -> np.ndarray:
        return np.asarray(
            cv2.boxFilter(array, -1, (window, window), borderType=cv2.BORDER_CONSTANT),
            dtype=np.float64,
        )

    ma, mb = mean(a), mean(b)
    va = np.maximum(mean(a * a) - ma * ma, 0.0)
    vb = np.maximum(mean(b * b) - mb * mb, 0.0)
    valid = (mean(support.astype(np.float64)) > 1.0 - 1e-12) & (va > 1e-12) & (vb > 1e-12)
    values = np.zeros(source.shape, np.float32)
    denominator = np.sqrt(va[valid] * vb[valid])
    values[valid] = np.clip((mean(a * b) - ma * mb)[valid] / denominator, -1.0, 1.0)
    values = np.frombuffer(values.tobytes(), dtype=np.float32).reshape(values.shape)
    valid = np.frombuffer(valid.tobytes(), dtype=np.bool_).reshape(valid.shape)
    return Correlation(values=values, valid=valid)


def affine_correlation(
    image: np.ndarray,
    matrix: np.ndarray,
    *,
    target_box: tuple[int, int, int, int],
    window: int = 7,
    pixel_support: np.ndarray | None = None,
) -> Correlation:
    """Compare a bounded target ROI with its affine-warped source counterpart.

    Coordinates are working-image pixel centers. The returned arrays cover target_box
    (y0, x0, y1, x1). A two-pixel source margin conservatively excludes cubic
    interpolation that would depend on synthesized border pixels.
    """
    y0, x0, y1, x1 = target_box
    if pixel_support is not None and (
        pixel_support.shape != image.shape or pixel_support.dtype != np.bool_
    ):
        raise InputRejectedError("affine pixel support must be aligned boolean pixels")
    if (
        image.ndim != 2
        or image.dtype.kind not in "uif"
        or not np.isfinite(image).all()
        or not (0 <= y0 < y1 <= image.shape[0] and 0 <= x0 < x1 <= image.shape[1])
        or (y1 - y0) * (x1 - x0) > 4_000_000
        or matrix.shape != (2, 3)
        or not np.isfinite(matrix).all()
    ):
        raise InputRejectedError("affine correlation needs a finite image, affine, and bounded ROI")
    linear = matrix[:, :2].astype(np.float64)
    determinant = float(np.linalg.det(linear))
    if not np.isfinite(determinant) or abs(determinant) <= 1e-8:
        raise InputRejectedError("affine correlation transform must be nonsingular")
    inverse = np.linalg.inv(linear)
    corners = np.array([[x0, y0], [x1 - 1, y0], [x0, y1 - 1], [x1 - 1, y1 - 1]])
    source_corners = (corners - matrix[:, 2]) @ inverse.T
    if not np.isfinite(source_corners).all():
        raise InputRejectedError("affine correlation source envelope is nonfinite")
    sx0 = max(0, int(np.floor(source_corners[:, 0].min())) - 3)
    sy0 = max(0, int(np.floor(source_corners[:, 1].min())) - 3)
    sx1 = min(image.shape[1], int(np.ceil(source_corners[:, 0].max())) + 4)
    sy1 = min(image.shape[0], int(np.ceil(source_corners[:, 1].max())) + 4)
    if sx0 >= sx1 or sy0 >= sy1:
        empty = np.zeros((y1 - y0, x1 - x0), np.float32)
        return Correlation(
            np.frombuffer(empty.tobytes(), np.float32).reshape(empty.shape),
            np.frombuffer(np.zeros(empty.shape, bool).tobytes(), bool).reshape(empty.shape),
        )
    if (sx1 - sx0) * (sy1 - sy0) > 4_000_000:
        raise InputRejectedError("affine source ROI exceeds four million pixels; no partial mask")
    local = matrix.astype(np.float64).copy()
    local[:, 2] += linear @ np.array([sx0, sy0]) - np.array([x0, y0])
    crop = image[sy0:sy1, sx0:sx1].astype(np.float64)
    target = image[y0:y1, x0:x1].astype(np.float64)
    scale = max(float(np.max(np.abs(crop))), float(np.max(np.abs(target))), 1.0)
    crop /= scale
    target /= scale
    warped = cv2.warpAffine(
        crop,
        local,
        (x1 - x0, y1 - y0),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
    )
    yy, xx = np.indices(target.shape, dtype=np.float64)
    source_x = inverse[0, 0] * (xx + x0 - matrix[0, 2]) + inverse[0, 1] * (yy + y0 - matrix[1, 2])
    source_y = inverse[1, 0] * (xx + x0 - matrix[0, 2]) + inverse[1, 1] * (yy + y0 - matrix[1, 2])
    support = (
        (source_x >= sx0 + 2)
        & (source_x <= sx1 - 3)
        & (source_y >= sy0 + 2)
        & (source_y <= sy1 - 3)
    )
    if pixel_support is not None:
        source_support = cv2.erode(
            pixel_support[sy0:sy1, sx0:sx1].astype(np.uint8),
            np.ones((5, 5), np.uint8),
            borderType=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        transported = cv2.warpAffine(
            source_support,
            local,
            (x1 - x0, y1 - y0),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
        )
        support &= (transported != 0) & pixel_support[y0:y1, x0:x1]
    return local_zncc(warped, target, support=support, window=window)

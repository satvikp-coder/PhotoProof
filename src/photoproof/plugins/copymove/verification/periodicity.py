"""Bounded actual-pixel autocorrelation for a possible repetitive-texture caution."""

import math

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.verification.schema import Config


def _period(
    values: list[float | None], *, displacement: float, minimum: float, phase_tolerance: float
) -> tuple[int, float] | None:
    for lag in range(2, len(values) - 1):
        current, previous, following = values[lag], values[lag - 1], values[lag + 1]
        history = [value for value in values[1:lag] if value is not None]
        if current is None or previous is None or following is None or not history:
            continue
        if (
            current >= minimum
            and current > previous
            and current >= following
            and min(history) < minimum - 0.15
            and abs(displacement - round(displacement / lag) * lag) <= phase_tolerance
        ):
            return lag, current
    return None


def inspect_repetition(
    image: np.ndarray,
    box: tuple[int, int, int, int],
    matrix: np.ndarray,
    *,
    config: Config,
    remaining_work: int,
) -> tuple[tuple[str, ...], dict[str, object], int]:
    """Inspect a bounded sampled source ROI for translation-aligned periodic peaks.

    Correlation is a texture statistic, not a manipulation probability. No original
    pixel buffer is modified. Rotation/scale repetition is not assessed here.
    """
    if (
        not isinstance(image, np.ndarray)
        or image.ndim not in (2, 3)
        or image.dtype.kind not in "uif"
        or (image.ndim == 3 and image.shape[2] not in (1, 3, 4))
    ):
        raise InputRejectedError("repetition inspection requires numeric working pixels")
    if (
        not isinstance(matrix, np.ndarray)
        or matrix.shape != (2, 3)
        or matrix.dtype.kind not in "uif"
        or not np.isfinite(matrix).all()
    ):
        raise InputRejectedError("repetition inspection requires a finite 2x3 working transform")
    if (
        not isinstance(config, Config)
        or isinstance(remaining_work, bool)
        or not isinstance(remaining_work, int)
        or remaining_work < 0
    ):
        raise InputRejectedError("repetition inspection needs validated config and work budget")
    if (
        not isinstance(box, (tuple, list))
        or len(box) != 4
        or any(isinstance(v, bool) or not isinstance(v, int) for v in box)
    ):
        raise InputRejectedError("repetition source box requires integer [y0,x0,y1,x1]")
    y0, x0, y1, x1 = box
    if not (0 <= y0 < y1 <= image.shape[0] and 0 <= x0 < x1 <= image.shape[1]):
        raise InputRejectedError("repetition source box is outside the working frame")
    if not np.allclose(matrix[:, :2], np.eye(2), rtol=0, atol=1e-3):
        return (), {"status": "not_translation"}, 0
    stride = max(1, math.ceil(max(y1 - y0, x1 - x0) / config.periodic_max_edge))
    pixels = np.array(image[y0:y1:stride, x0:x1:stride], dtype=np.float64, copy=True)
    if not np.isfinite(pixels).all():
        raise InputRejectedError("repetition source pixels must be finite")
    if min(pixels.shape[:2]) < 8:
        return ("repetition_check_unavailable",), {"status": "region_too_small"}, 0
    maximum = float(np.abs(pixels).max())
    if maximum:
        pixels /= maximum
    if pixels.ndim == 2:
        gray = pixels
    elif pixels.shape[2] == 1:
        gray = pixels[:, :, 0]
    else:
        gray = pixels[:, :, :3] @ np.array([0.2126, 0.7152, 0.0722])
    gray -= float(gray.mean())
    energy = float(np.sum(gray * gray))
    if energy <= 1e-12 * gray.size:
        return ("insufficient_texture",), {"status": "insufficient_texture"}, 0
    height, width = gray.shape
    shape = (1 << (2 * height - 1).bit_length(), 1 << (2 * width - 1).bit_length())
    work = 2 * shape[0] * shape[1] * ((shape[0].bit_length() - 1) + (shape[1].bit_length() - 1))
    if work > remaining_work:
        raise InputRejectedError("verification texture work limit exceeded; reduce input clusters")
    spectrum = np.fft.rfft2(gray, s=shape, axes=(-2, -1))
    correlation = np.fft.irfft2(spectrum.real**2 + spectrum.imag**2, s=shape, axes=(-2, -1))
    column_energy = np.concatenate(([0.0], np.cumsum(np.sum(gray * gray, axis=0))))
    row_energy = np.concatenate(([0.0], np.cumsum(np.sum(gray * gray, axis=1))))

    def profile(axis: int) -> list[float | None]:
        energies = column_energy if axis == 1 else row_energy
        length = width if axis == 1 else height
        limit = min(config.periodic_max_lag + 1, length // 2 + 1, length - 1)
        result: list[float | None] = [1.0]
        for lag in range(1, limit + 1):
            first = float(energies[length - lag])
            second = float(energies[length] - energies[lag])
            denominator = math.sqrt(max(0.0, first * second))
            numerator = float(correlation[0, lag] if axis == 1 else correlation[lag, 0])
            result.append(max(-1.0, min(1.0, numerator / denominator)) if denominator else None)
        return result

    dx, dy = float(matrix[0, 2]), float(matrix[1, 2])
    x_period = _period(
        profile(1),
        displacement=dx / stride,
        minimum=config.periodic_min_corr,
        phase_tolerance=max(0.5, 1 / stride),
    )
    y_period = _period(
        profile(0),
        displacement=dy / stride,
        minimum=config.periodic_min_corr,
        phase_tolerance=max(0.5, 1 / stride),
    )
    aligned = (
        (abs(dx) <= 0.5 or x_period is not None)
        and (abs(dy) <= 0.5 or y_period is not None)
        and (x_period is not None or y_period is not None)
    )
    return (
        ("possible_repetitive_structure",) if aligned else (),
        {
            "status": "checked",
            "method": "linear_fft_autocorrelation",
            "sample_shape": [height, width],
            "stride": stride,
            "period_x_working": x_period[0] * stride if x_period else None,
            "period_y_working": y_period[0] * stride if y_period else None,
            "correlation_x": x_period[1] if x_period else None,
            "correlation_y": y_period[1] if y_period else None,
        },
        work,
    )

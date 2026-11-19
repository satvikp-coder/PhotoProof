"""Texture cautions depend on actual pixels and stay bounded and non-mutating."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.verification import periodicity
from photoproof.plugins.copymove.verification.schema import Config


def _matrix(dx=64, dy=32):
    return np.array([[1, 0, dx], [0, 1, dy]], np.float64)


def test_random_pixels_are_not_flagged_just_because_displacement_is_constant():
    image = np.random.default_rng(23).integers(0, 256, (128, 128, 3), np.uint8)
    flags, info, _ = periodicity.inspect_repetition(
        image, (0, 0, 128, 128), _matrix(), config=Config(), remaining_work=64_000_000
    )
    assert flags == () and info["status"] == "checked"


def test_actual_periodic_texture_and_phase_alignment_are_checked():
    yy, xx = np.indices((128, 128))
    image = (((xx // 8 + yy // 8) % 2) * 60_000 + 5_000).astype(np.uint16)
    image.flags.writeable = False
    original = image.tobytes()
    flags, info, _ = periodicity.inspect_repetition(
        image, (0, 0, 128, 128), _matrix(), config=Config(), remaining_work=64_000_000
    )
    assert flags == ("possible_repetitive_structure",)
    assert info["period_x_working"] == info["period_y_working"] == 16
    flags, _, _ = periodicity.inspect_repetition(
        image, (0, 0, 128, 128), _matrix(dx=70), config=Config(), remaining_work=64_000_000
    )
    assert flags == () and image.tobytes() == original


def test_unobservable_lags_cannot_become_zero_correlation_evidence():
    assert (
        periodicity._period(
            [1, None, None, 1, None], displacement=3, minimum=0.85, phase_tolerance=1
        )
        is None
    )


def test_flat_and_tiny_regions_are_reported_as_insufficient_not_clean():
    for box, expected in [
        ((0, 0, 64, 64), "insufficient_texture"),
        ((0, 0, 4, 4), "repetition_check_unavailable"),
    ]:
        flags, _, work = periodicity.inspect_repetition(
            np.zeros((64, 64), np.uint8), box, _matrix(), config=Config(), remaining_work=0
        )
        assert flags == (expected,) and work == 0


def test_fft_work_guard_precedes_transform_allocation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("FFT cannot run over its work budget")

    monkeypatch.setattr(periodicity.np.fft, "rfft2", forbidden)
    image = np.random.default_rng(23).integers(0, 256, (512, 512), np.uint8)
    with pytest.raises(InputRejectedError, match="texture work limit"):
        periodicity.inspect_repetition(
            image, (0, 0, 512, 512), _matrix(), config=Config(), remaining_work=1
        )


@pytest.mark.parametrize("box", [(-1, 0, 64, 64), (0, 0, 65, 64), (0.5, 0, 64, 64)])
def test_invalid_region_geometry_is_rejected(box):
    with pytest.raises(InputRejectedError):
        periodicity.inspect_repetition(
            np.zeros((64, 64), np.uint8), box, _matrix(), config=Config(), remaining_work=1
        )

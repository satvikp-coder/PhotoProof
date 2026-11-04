"""Tests for photoproof.imaging.stats: block tiling and robust statistics."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.stats import (
    anomaly_map,
    block_reduce,
    robust_zscore,
    tile,
    upsample,
)


def test_tile_non_overlapping_drop() -> None:
    arr = np.arange(16, dtype=np.float32).reshape(4, 4)
    windows, specs = tile(arr, block=(2, 2), border="drop")
    assert windows.shape == (2, 2, 2, 2)
    assert len(specs) == 4
    assert np.array_equal(windows[0, 0], arr[0:2, 0:2])
    assert np.array_equal(windows[0, 1], arr[0:2, 2:4])
    assert np.array_equal(windows[1, 0], arr[2:4, 0:2])
    assert np.array_equal(windows[1, 1], arr[2:4, 2:4])
    assert specs[0].origin == (0, 0)
    assert specs[1].origin == (0, 2)
    assert specs[2].origin == (2, 0)
    assert specs[3].origin == (2, 2)


def test_tile_overlapping() -> None:
    arr = np.arange(16, dtype=np.float32).reshape(4, 4)
    windows, specs = tile(arr, block=(2, 2), stride=(1, 1), border="drop")
    assert windows.shape == (3, 3, 2, 2)
    assert len(specs) == 9


def test_tile_pad_reflect() -> None:
    arr = np.arange(9, dtype=np.float32).reshape(3, 3)
    windows, _specs = tile(arr, block=(2, 2), stride=(2, 2), border="pad_reflect")
    assert windows.shape == (2, 2, 2, 2)
    # Edge blocks are padded via reflection.
    assert windows[0, 0].shape == (2, 2)
    assert windows[1, 1].shape == (2, 2)


def test_tile_smaller_than_block() -> None:
    arr = np.zeros((3, 3), dtype=np.float32)
    windows, specs = tile(arr, block=(8, 8), border="drop")
    assert windows.shape == (0, 0, 8, 8)
    assert specs == []


def test_tile_invalid_inputs() -> None:
    arr = np.zeros((8, 8), dtype=np.float32)
    with pytest.raises(InputRejectedError):
        tile(arr, block=(0, 8))
    with pytest.raises(InputRejectedError):
        tile(arr, block=(8, 8), stride=(0, 8))
    with pytest.raises(InputRejectedError):
        tile(arr, block=(8, 8), border="invalid")  # type: ignore[arg-type]
    with pytest.raises(InputRejectedError):
        tile(np.zeros((8, 8, 3), dtype=np.float32), block=(2, 2))


def test_block_reduce_mean() -> None:
    arr = np.arange(16, dtype=np.float32).reshape(4, 4)
    out = block_reduce(arr, block=(2, 2), func=lambda b: float(np.mean(b)))
    assert out.shape == (2, 2)
    assert out[0, 0] == np.mean(arr[0:2, 0:2])
    assert out[0, 1] == np.mean(arr[0:2, 2:4])
    assert out.dtype == np.float32


def test_block_reduce_median() -> None:
    arr = np.arange(16, dtype=np.float32).reshape(4, 4)
    out = block_reduce(arr, block=(2, 2), func=lambda b: float(np.median(b)))
    assert out.shape == (2, 2)


def test_robust_zscore_normal() -> None:
    rng = np.random.default_rng(42)
    values = rng.normal(0, 1, 1000).astype(np.float32)
    z, degenerate = robust_zscore(values)
    assert not degenerate
    assert z.shape == values.shape
    assert np.abs(np.median(z)) < 0.1  # median of z-scores ~ 0
    assert 0.8 < np.std(z) < 1.2  # std of z-scores ~ 1


def test_robust_zscore_constant_degenerate() -> None:
    values = np.full(100, 5.0, dtype=np.float32)
    z, degenerate = robust_zscore(values)
    assert degenerate
    assert np.all(z == 0.0)


def test_robust_zscore_mad_zero_degenerate() -> None:
    # All values identical: MAD = 0, returns zeros with degenerate=True
    values = np.full(10, 1.0, dtype=np.float32)
    z, degenerate = robust_zscore(values)
    assert degenerate
    assert np.all(z == 0.0)


def test_robust_zscore_outlier_detected() -> None:
    # MAD > 0 because more than half the values differ from the median
    values = np.array([1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 3.0, 100.0], dtype=np.float32)
    z, degenerate = robust_zscore(values)
    assert not degenerate
    assert z.shape == values.shape
    # The extreme outlier gets the highest |z|
    assert np.argmax(np.abs(z)) == 9


def test_robust_zscore_mask() -> None:
    values = np.array([1.0, 2.0, 3.0, 100.0], dtype=np.float32)
    mask = np.array([True, True, True, False], dtype=bool)
    z, degenerate = robust_zscore(values, mask=mask)
    assert not degenerate
    assert z[3] == 0.0  # masked value gets z=0


def test_robust_zscore_empty() -> None:
    values = np.array([], dtype=np.float32)
    z, degenerate = robust_zscore(values)
    assert degenerate
    assert z.size == 0


def test_robust_zscore_mask_empty() -> None:
    values = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    mask = np.array([False, False, False], dtype=bool)
    z, degenerate = robust_zscore(values, mask=mask)
    assert degenerate
    assert np.all(z == 0.0)


def test_anomaly_map_defaults() -> None:
    values = np.ones((4, 4), dtype=np.float32)
    m = anomaly_map(values, block=(2, 2), stride=(2, 2))
    assert m.scale == "robust_z"
    assert m.block == (2, 2)
    assert m.stride == (2, 2)
    assert m.valid_mask.all()
    assert m.notes == ()


def test_anomaly_map_with_notes() -> None:
    values = np.ones((2, 2), dtype=np.float32)
    m = anomaly_map(values, block=(2, 2), stride=(2, 2), notes=("degenerate", "too_small"))
    assert m.notes == ("degenerate", "too_small")


def test_upsample_nearest() -> None:
    values = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    m = anomaly_map(values, block=(2, 2), stride=(2, 2))
    out = upsample(m, image_shape=(4, 4), mode="nearest")
    assert out.shape == (4, 4)
    assert np.all(out[0:2, 0:2] == 1.0)
    assert np.all(out[0:2, 2:4] == 2.0)
    assert np.all(out[2:4, 0:2] == 3.0)
    assert np.all(out[2:4, 2:4] == 4.0)


def test_upsample_invalid_mode() -> None:
    values = np.ones((2, 2), dtype=np.float32)
    m = anomaly_map(values, block=(2, 2), stride=(2, 2))
    with pytest.raises(InputRejectedError):
        upsample(m, image_shape=(4, 4), mode="cubic")  # type: ignore[arg-type]


def test_planting_outlier_gets_highest_z() -> None:
    """Acceptance: planted outlier block gets highest |z|."""
    rng = np.random.default_rng(7)
    base = rng.normal(0, 1, (128, 128)).astype(np.float32)
    # Plant a strong outlier block aligned to block boundaries
    base[32:48, 32:48] += 50.0

    out = block_reduce(base, block=(16, 16), func=lambda b: float(np.mean(b)))
    z, degenerate = robust_zscore(out)
    assert not degenerate

    outlier_block = (2, 2)  # block covering rows 32-47, cols 32-47
    assert np.abs(z[outlier_block]) > 10.0
    assert np.argmax(np.abs(z)) == outlier_block[0] * z.shape[1] + outlier_block[1]

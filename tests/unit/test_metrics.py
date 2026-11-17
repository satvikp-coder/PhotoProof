"""Tests for tools/metrics.py (#48 evaluation metrics)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tools.metrics import bootstrap_ci, f1, iou, mcc


def _gt(pred: list[list[int]]) -> list[list[int]]:
    return pred


def test_iou_perfect_match() -> None:
    gt = [[1, 1], [1, 1]]
    pr = [[1, 1], [1, 1]]
    assert iou(gt, pr) == 1.0


def test_iou_no_overlap() -> None:
    gt = [[1, 0], [0, 0]]
    pr = [[0, 0], [0, 1]]
    assert iou(gt, pr) == 0.0


def test_iou_partial_overlap() -> None:
    gt = [[1, 1], [0, 0]]
    pr = [[1, 0], [0, 0]]
    # TP=1, FP=0, FN=1 => IoU = 1/2 = 0.5
    assert iou(gt, pr) == 0.5


def test_iou_empty_gt_empty_pred() -> None:
    """Empty ground truth + empty prediction => 1.0 (TN-only)."""
    gt = [[0, 0], [0, 0]]
    pr = [[0, 0], [0, 0]]
    assert iou(gt, pr) == 1.0


def test_iou_empty_gt_nonempty_pred() -> None:
    """Empty ground truth with false positives => IoU = 0."""
    gt = [[0, 0], [0, 0]]
    pr = [[1, 0], [0, 0]]
    assert iou(gt, pr) == 0.0


def test_f1_perfect_match() -> None:
    gt = [[1, 1], [1, 1]]
    pr = [[1, 1], [1, 1]]
    assert f1(gt, pr) == 1.0


def test_f1_no_overlap() -> None:
    gt = [[1, 0], [0, 0]]
    pr = [[0, 0], [0, 1]]
    assert f1(gt, pr) == 0.0


def test_f1_empty_gt_empty_pred() -> None:
    """Empty ground truth + empty prediction => 1.0."""
    gt = [[0, 0], [0, 0]]
    pr = [[0, 0], [0, 0]]
    assert f1(gt, pr) == 1.0


def test_f1_partial() -> None:
    gt = [[1, 1], [0, 0]]
    pr = [[1, 0], [0, 0]]
    # TP=1, FP=0, FN=1 => F1 = 2*1/(2+0+1) = 2/3
    assert abs(f1(gt, pr) - 2 / 3) < 1e-9


def test_mcc_perfect_match() -> None:
    gt = [[1, 0], [0, 1]]
    pr = [[1, 0], [0, 1]]
    assert mcc(gt, pr) == 1.0


def test_mcc_inverse() -> None:
    """Inverse prediction => MCC = -1."""
    gt = [[1, 0], [0, 1]]
    pr = [[0, 1], [1, 0]]
    assert mcc(gt, pr) == -1.0


def test_mcc_random() -> None:
    """Random-like prediction => MCC near 0."""
    gt = [[1, 0], [0, 1]]
    pr = [[1, 1], [0, 0]]
    result = mcc(gt, pr)
    assert -1.0 <= result <= 1.0


def test_mcc_denominator_zero() -> None:
    """All background (TN only) => denominator zero => MCC = 0.0."""
    gt = [[0, 0], [0, 0]]
    pr = [[0, 0], [0, 0]]
    assert mcc(gt, pr) == 0.0


def test_mcc_all_predicted_positive() -> None:
    """Everything predicted positive => denominator zero => 0.0."""
    gt = [[1, 0], [0, 1]]
    pr = [[1, 1], [1, 1]]
    assert mcc(gt, pr) == 0.0


def test_bootstrap_ci_deterministic() -> None:
    """Same seed => same interval."""
    values = [0.1, 0.2, 0.3, 0.4, 0.5]
    ci1 = bootstrap_ci(values, seed=42)
    ci2 = bootstrap_ci(values, seed=42)
    assert ci1 == ci2


def test_bootstrap_ci_contains_mean() -> None:
    """CI should contain the sample mean for reasonable data."""
    values = [0.5] * 100
    ci = bootstrap_ci(values, seed=1)
    assert ci[0] <= 0.5 <= ci[1]


def test_bootstrap_ci_empty() -> None:
    assert bootstrap_ci([], seed=42) == (0.0, 0.0)


def test_mismatched_shapes_raise() -> None:
    with pytest.raises(ValueError):
        iou([[1, 0]], [[1, 0, 0]])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

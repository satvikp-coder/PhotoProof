"""Forensic evaluation metrics: IoU, F1, MCC with hand-verifiable edge cases.

All functions operate on binary masks (0 = background, non-zero = foreground)
and return floats in [0, 1] (MCC in [-1, 1]).  Edge cases are documented:
- Empty ground truth + empty prediction  ->  TN-only, score = 1.0 (for IoU/F1)
- MCC denominator zero                  ->  0.0 (undefined correlation)
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def _confusion_matrix(
    ground_truth: Sequence[Sequence[int]], prediction: Sequence[Sequence[int]]
) -> tuple[int, int, int, int]:
    """Return (TP, FP, FN, TN) for two equal-shape binary masks."""
    if len(ground_truth) != len(prediction):
        raise ValueError("ground truth and prediction must have the same shape")
    tp = fp = fn = tn = 0
    for gt_row, pred_row in zip(ground_truth, prediction, strict=False):
        if len(gt_row) != len(pred_row):
            raise ValueError("ground truth and prediction rows must have equal length")
        for gt_val, pred_val in zip(gt_row, pred_row, strict=False):
            gt_pos = gt_val != 0
            pred_pos = pred_val != 0
            if gt_pos and pred_pos:
                tp += 1
            elif pred_pos and not gt_pos:
                fp += 1
            elif gt_pos and not pred_pos:
                fn += 1
            else:
                tn += 1
    return tp, fp, fn, tn


def iou(ground_truth: Sequence[Sequence[int]], prediction: Sequence[Sequence[int]]) -> float:
    """Intersection over Union.

    Empty ground truth + empty prediction => 1.0 (TN-only, perfect agreement
    that there is nothing to detect).
    """
    tp, fp, fn, _ = _confusion_matrix(ground_truth, prediction)
    union = tp + fp + fn
    if union == 0:
        return 1.0
    return tp / union


def f1(ground_truth: Sequence[Sequence[int]], prediction: Sequence[Sequence[int]]) -> float:
    """F1 score (harmonic mean of precision and recall).

    Empty ground truth + empty prediction => 1.0.
    """
    tp, fp, fn, _ = _confusion_matrix(ground_truth, prediction)
    denom = 2 * tp + fp + fn
    if denom == 0:
        return 1.0
    return 2 * tp / denom


def mcc(ground_truth: Sequence[Sequence[int]], prediction: Sequence[Sequence[int]]) -> float:
    """Matthews Correlation Coefficient.

    Range [-1, 1]: +1 = perfect prediction, 0 = random, -1 = inverse.
    Denominator zero => 0.0 (undefined correlation).
    """
    tp, fp, fn, tn = _confusion_matrix(ground_truth, prediction)
    numer = tp * tn - fp * fn
    denom = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    if denom == 0:
        return 0.0
    return numer / denom


def bootstrap_ci(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
    n_bootstrap: int = 2000,
    seed: int = 42,
) -> tuple[float, float]:
    """Bootstrap confidence interval for the mean of ``values``.

    Deterministic given ``seed``.  Returns (lower, upper).
    """
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    rng = random.Random(seed)  # noqa: S311
    alpha = 1.0 - confidence
    lower_pct = 100 * alpha / 2
    upper_pct = 100 * (1 - alpha / 2)
    means: list[float] = []
    for _ in range(n_bootstrap):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lower_idx = int(lower_pct / 100 * (n_bootstrap - 1))
    upper_idx = int(upper_pct / 100 * (n_bootstrap - 1))
    return (means[lower_idx], means[upper_idx])

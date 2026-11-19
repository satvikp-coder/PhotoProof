"""Non-vacuous candidate recovery and native-call boundary regressions."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.matching import algorithm


def _features():
    keypoints = np.zeros((8, 5), dtype=np.float32)
    keypoints[:, 0] = np.arange(8) * 100
    descriptors = np.random.default_rng(21).normal(2, 1, (8, 128)).astype(np.float32)
    descriptors[:3] = 0
    return keypoints, descriptors


def test_multiple_identical_copies_are_recovered_before_a_positive_distance_gap():
    keypoints, descriptors = _features()
    result = algorithm.analyze_sift_matching(keypoints, descriptors, k=5)
    pairs = {tuple(sorted(pair)) for pair in result["pairs"].tolist()}
    assert {(0, 1), (0, 2), (1, 2)} <= pairs


def test_nearby_structure_does_not_consume_the_candidate_limit():
    keypoints, descriptors = _features()
    keypoints[1, 0] = 1
    result = algorithm.analyze_sift_matching(keypoints, descriptors, k=5, max_pairs=1)
    assert result["pairs"].tolist() == [[0, 2]]


@pytest.mark.parametrize(
    "params",
    [
        {"k": 20000},
        {"k": True},
        {"ratio": float("nan")},
        {"min_spatial_distance": float("inf")},
        {"max_pairs": 0},
    ],
)
def test_invalid_matching_parameters_never_enter_opencv(monkeypatch, params):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid matching parameters must fail before native matching")

    monkeypatch.setattr(algorithm, "_brute_force_knn_match", forbidden)
    keypoints, descriptors = _features()
    with pytest.raises(InputRejectedError):
        algorithm.analyze_sift_matching(keypoints, descriptors, **params)


def test_nonfinite_descriptors_are_rejected_before_opencv(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("nonfinite features must fail before native matching")

    monkeypatch.setattr(algorithm, "_brute_force_knn_match", forbidden)
    keypoints, descriptors = _features()
    descriptors[0, 0] = np.nan
    with pytest.raises(InputRejectedError, match="finite"):
        algorithm.analyze_sift_matching(keypoints, descriptors)

"""Recorded detection transforms map actual per-axis pixel centres correctly."""

import numpy as np
import pytest

from photoproof.plugins.copymove.sift_keypoints import algorithm


def test_rounded_resize_maps_centres_angles_and_area_equivalent_scale(monkeypatch):
    def markers(image, *args):
        assert image.shape == (33, 100)
        return np.array([[1, 2, 3, 45, 2]], np.float32), np.ones((1, 128), np.float32)

    monkeypatch.setattr(algorithm, "_extract_sift", markers)
    result = algorithm.analyze_sift_keypoints(np.zeros((100, 301), np.uint8), max_edge=100)
    xratio, yratio = 100 / 301, 33 / 100
    point = result["keypoints"][0]
    np.testing.assert_allclose(point[:2], [(1.5 / xratio) - 0.5, (2.5 / yratio) - 0.5], atol=1e-5)
    np.testing.assert_allclose(point[2], 3 / np.sqrt(xratio * yratio), atol=1e-5)
    angle = np.rad2deg(np.arctan2(np.sin(np.pi / 4) / yratio, np.cos(np.pi / 4) / xratio))
    np.testing.assert_allclose(point[3], angle, atol=1e-5)
    matrix = np.array(result["config"]["detection_to_working"])
    np.testing.assert_allclose((matrix @ [1, 2, 1])[:2], point[:2], atol=1e-5)
    assert result["config"]["detection_size"] == [100, 33]


def test_narrow_images_never_request_a_zero_sized_resize():
    resized, _ = algorithm._resize_for_detection(np.zeros((1, 1000), np.uint8), 100)
    assert resized.shape == (1, 100)


def test_response_cap_keeps_strongest_features_and_aligned_descriptors(monkeypatch):
    def markers(*args):
        points = np.array([[5, 5, 1, 0, 1], [6, 6, 1, 0, 10], [7, 7, 1, 0, 5]], np.float32)
        return points, np.repeat(np.arange(3, dtype=np.float32)[:, None], 128, axis=1)

    monkeypatch.setattr(algorithm, "_extract_sift", markers)
    result = algorithm.analyze_sift_keypoints(np.zeros((64, 64), np.uint8), max_keypoints=2)
    np.testing.assert_array_equal(result["keypoints"][:, 4], [10, 5])
    np.testing.assert_array_equal(result["descriptors"][:, 0], [1, 2])


@pytest.mark.parametrize(
    "count,confidence", [(0, "low"), (49, "low"), (50, "medium"), (100, "high")]
)
def test_fewer_than_fifty_features_always_has_low_confidence(monkeypatch, count, confidence):
    def markers(*args):
        return np.zeros((count, 5), np.float32), np.zeros((count, 128), np.float32)

    monkeypatch.setattr(algorithm, "_extract_sift", markers)
    result = algorithm.analyze_sift_keypoints(np.zeros((64, 64), np.uint8))
    assert result["confidence"] == confidence


def test_grayscale_conversion_clips_instead_of_wrapping_intensities():
    result = algorithm._to_grayscale(np.array([[-1, 0, 255, 256]], dtype=np.float32))
    np.testing.assert_array_equal(result, [[0, 0, 255, 255]])

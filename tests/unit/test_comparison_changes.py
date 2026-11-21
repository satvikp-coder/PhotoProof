"""Measured-difference synthetic controls; not a manipulation benchmark."""

import io

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.plugins.comparison.change_mask.algorithm import extract_changes
from photoproof.plugins.comparison.change_mask.schema import Config
from photoproof.plugins.comparison.ssim.algorithm import compare_aligned


def _edit():
    rng = np.random.default_rng(30)
    reference = rng.integers(30, 190, (192, 256), np.uint8)
    target = reference.copy()
    truth = np.zeros(reference.shape, bool)
    truth[65:125, 85:165] = True
    target[truth] = 255 - reference[truth]
    similarity = compare_aligned(reference, target, np.ones(reference.shape, bool))
    return similarity, truth


def test_clean_edit_iou_and_real_region_statistics():
    similarity, truth = _edit()
    result = extract_changes(similarity.dissimilarity, similarity.valid_mask)
    iou = np.count_nonzero(result.mask & truth) / np.count_nonzero(result.mask | truth)
    assert iou >= 0.7, iou
    assert result.summary["status"] == "differences_detected"
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region["area"] == result.mask.sum()
    assert region["category"] == result.summary["category"] == "difference"
    assert region["mean_dissimilarity"] == pytest.approx(
        similarity.dissimilarity[result.mask].mean(), abs=1e-6
    )
    assert not result.mask.flags.writeable
    assert not result.mask[~similarity.valid_mask].any()


@pytest.mark.parametrize("quality", [75, 85, 95])
def test_recompressed_identical_content_has_no_regions_at_default_sensitivity(quality):
    rng = np.random.default_rng(300)
    reference = rng.integers(0, 256, (128, 160), np.uint8)
    stream = io.BytesIO()
    Image.fromarray(reference).save(stream, format="JPEG", quality=quality)
    with Image.open(io.BytesIO(stream.getvalue())) as image:
        target = np.asarray(image).copy()
    measured = compare_aligned(reference, target, np.ones(reference.shape, bool))
    result = extract_changes(measured.dissimilarity, measured.valid_mask)
    assert result.summary["status"] == "no_differences_detected"
    assert not result.mask.any() and not result.regions


def test_identical_scores_do_not_become_changes_at_zero_threshold():
    scores = np.zeros((64, 64), np.float32)
    result = extract_changes(scores, np.ones(scores.shape, bool), config=Config(threshold=0))
    assert result.summary["status"] == "no_differences_detected"


def test_unknown_support_is_not_no_differences():
    scores = np.zeros((64, 64), np.float32)
    with pytest.raises(NotApplicableError):
        extract_changes(scores, np.zeros(scores.shape, bool))


def test_cleanup_never_fills_unknown_holes():
    scores = np.ones((64, 64), np.float32) * 0.8
    valid = np.ones(scores.shape, bool)
    valid[20:25, 20:25] = False
    scores[~valid] = 0
    result = extract_changes(scores, valid)
    assert not result.mask[~valid].any()
    assert result.summary["low_specificity"]


def test_small_component_filter_and_metadata_truncation_preserve_full_raster():
    scores = np.zeros((64, 64), np.float32)
    scores[5:10, 5:10] = 0.8
    scores[20:25, 20:25] = 0.9
    scores[40, 40] = 0.8
    cfg = Config(threshold=0.5, kernel_size=1, min_region_area=4 / scores.size, max_regions=1)
    result = extract_changes(scores, np.ones(scores.shape, bool), config=cfg)
    assert result.mask.sum() == 50
    assert not result.mask[40, 40]
    assert result.summary["region_count"] == 2
    assert result.summary["regions_truncated"]
    assert result.summary["removed_small_components"] == 1
    assert len(result.regions) == 1
    assert result.regions[0]["bbox"] == [5, 5, 5, 5]


def test_automatic_threshold_honors_floor_but_explicit_threshold_is_respected():
    scores = np.zeros((64, 64), np.float32)
    scores[10:30, 10:30] = 0.1
    valid = np.ones(scores.shape, bool)
    automatic = extract_changes(scores, valid)
    explicit = extract_changes(scores, valid, config=Config(threshold=0.05))
    assert automatic.summary["threshold"] >= 0.15
    assert automatic.summary["status"] == "no_differences_detected"
    assert explicit.summary["status"] == "differences_detected"


def test_otsu_separates_two_actual_dissimilarity_modes():
    scores = np.ones((64, 64), np.float32) * 0.25
    scores[:, 32:] = 0.8
    result = extract_changes(scores, np.ones(scores.shape, bool), config=Config(kernel_size=1))
    assert 0.24 <= result.summary["threshold"] <= 0.26
    assert not result.mask[:, :32].any() and result.mask[:, 32:].all()


@pytest.mark.parametrize(
    "kind", ["nan", "range", "integer", "mask", "shape", "invalid_fill", "oversize"]
)
def test_rejects_malformed_or_unbounded_measurements(kind):
    scores = np.zeros((32, 32), np.float32)
    valid = np.ones(scores.shape, bool)
    cfg = Config()
    if kind == "nan":
        scores[0, 0] = np.nan
    elif kind == "range":
        scores[0, 0] = -0.1
    elif kind == "integer":
        scores = scores.astype(np.uint8)
    elif kind == "mask":
        valid = valid.astype(np.uint8)
    elif kind == "shape":
        valid = valid[:-1]
    elif kind == "invalid_fill":
        valid[0, 0] = False
        scores[0, 0] = 0.8
    else:
        cfg = Config(max_pixels=100)
    with pytest.raises(InputRejectedError):
        extract_changes(scores, valid, config=cfg)


@pytest.mark.parametrize(
    "values",
    [
        {"threshold": True},
        {"threshold": float("nan")},
        {"threshold": 1.1},
        {"kernel_size": 4},
        {"min_region_area": 0},
        {"max_regions": 1001},
        {"max_pixels": 4194305},
    ],
)
def test_invalid_configuration(values):
    with pytest.raises(ValidationError):
        Config(**values)

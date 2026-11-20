"""Seeded geometric fixtures validate alignment; differences do not identify forgery."""

import json

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.comparison.registration.algorithm import (
    _project,
    _quality,
    _warp_reference,
    register_pair,
)
from photoproof.plugins.comparison.registration.schema import Config
from tests.fixtures.synthetic import textured_scene


def _known(model):
    if model == "similarity":
        affine = cv2.getRotationMatrix2D((256, 192), 5, 0.94)
        affine[:, 2] += [8, -4]
        return np.vstack([affine, [0, 0, 1]])
    if model == "affine":
        return np.array([[0.98, 0.03, 7], [-0.02, 1.01, 9], [0, 0, 1]], np.float64)
    return np.array([[1, 0.025, 8], [-0.02, 1, 5], [0.00008, -0.00006, 1]], np.float64)


@pytest.mark.parametrize("model", ["similarity", "affine", "homography"])
@pytest.mark.parametrize("seed", [28, 29])
def test_known_transform_recovered_with_corner_error_below_one_point_five_pixels(model, seed):
    reference = textured_scene(384, 512, seed=seed)
    expected = _known(model)
    target = cv2.warpPerspective(reference, expected, (512, 384))
    noise = np.random.default_rng(seed).normal(0, 0.8, target.shape)
    target = np.clip(target.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    before_r, before_t = reference.copy(), target.copy()
    reference.setflags(write=False)
    target.setflags(write=False)
    result = register_pair(reference, target, config=Config(model=model), seed=seed)
    assert result.status == "registered", result.quality
    corners = np.array([[0, 0], [511, 0], [511, 383], [0, 383]], np.float64)
    error = np.linalg.norm(_project(corners, result.matrix) - _project(corners, expected), axis=1)
    print(f"{model} seed{seed} maximum corner error={error.max():.6f}")
    assert error.max() <= 1.5
    assert result.quality["inliers"] >= 12
    assert result.quality["reprojection_rmse"] <= 2
    assert result.overlap_mask.dtype == bool and result.overlap_mask.shape == (384, 512)
    assert result.overlap_mask.mean() > 0.7
    assert not result.aligned_reference[~result.overlap_mask].any()
    assert all(
        not a.flags.writeable
        for a in [
            result.matrix,
            result.aligned_reference,
            result.overlap_mask,
            result.inlier_points,
        ]
    )
    assert np.array_equal(reference, before_r) and np.array_equal(target, before_t)
    json.dumps(dict(result.quality), allow_nan=False)


def test_unrelated_images_fail_without_unaligned_fallback():
    result = register_pair(textured_scene(256, 320, seed=401), textured_scene(256, 320, seed=802))
    assert result.status == "registration_failed", result.quality
    assert result.matrix is result.aligned_reference is result.overlap_mask is None
    assert result.inlier_points.shape == (0, 4)


def test_repeatability_and_actual_optional_ecc_refinement():
    reference = textured_scene(256, 320, seed=281)
    expected = np.array([[1, 0, 9.25], [0, 1, -4.5], [0, 0, 1]], np.float64)
    target = cv2.warpPerspective(reference, expected, (320, 256))
    cfg = Config(model="similarity", refine_ecc=True)
    first = register_pair(reference, target, config=cfg, seed=19)
    second = register_pair(reference, target, config=cfg, seed=19)
    assert first.status == second.status == "registered"
    assert first.quality["ecc_status"] == "accepted", first.quality
    corners = np.array([[0, 0], [319, 0], [319, 255], [0, 255]], np.float64)
    assert (
        np.linalg.norm(_project(corners, first.matrix) - _project(corners, expected), axis=1).max()
        < 1.5
    )
    assert np.array_equal(first.matrix, second.matrix)
    assert np.array_equal(first.aligned_reference, second.aligned_reference)
    assert np.array_equal(first.overlap_mask, second.overlap_mask)


def test_ecc_failure_preserves_only_the_validated_initial_alignment(monkeypatch):
    reference = textured_scene(192, 256, seed=282)
    target = cv2.warpPerspective(
        reference, np.array([[1, 0, 8], [0, 1, 5], [0, 0, 1.0]]), (256, 192)
    )
    initial = register_pair(reference, target, config=Config(model="affine"))

    def fail(*args, **kwargs):
        raise cv2.error("controlled ECC nonconvergence")

    monkeypatch.setattr(cv2, "findTransformECC", fail)
    refined = register_pair(reference, target, config=Config(model="affine", refine_ecc=True))
    assert initial.status == refined.status == "registered"
    assert refined.quality["ecc_status"] == "not_converged"
    assert np.array_equal(initial.matrix, refined.matrix)
    assert not np.array_equal(initial.matrix, np.eye(3))


def test_overlap_excludes_unknown_alpha_and_fractional_outside_donors():
    reference = np.full((64, 64, 4), 255, np.uint8)
    target = reference.copy()
    reference[10:20, 10:20, 3] = 0
    target[40:50, 40:50, 3] = 0
    matrix = np.array([[1, 0, 3.4], [0, 1, 5], [0, 0, 1]], np.float64)
    aligned, valid = _warp_reference(reference, target, matrix=matrix)
    assert not valid[:5].any() and not valid[:, :4].any()
    assert not valid[16:24, 15:23].any()
    assert not valid[40:50, 40:50].any()
    assert not aligned[~valid].any() and valid[30, 30]
    _, tiny = _warp_reference(
        reference, target, matrix=np.array([[1, 0, 0.01], [0, 1, 0], [0, 0, 1.0]])
    )
    assert not tiny[:, 0].any()  # OpenCV's interpolation rounding must not fabricate coverage.


def test_degenerate_and_reflected_transforms_have_explicit_quality_rejections():
    x, y = np.meshgrid(np.linspace(30, 220, 6), np.linspace(30, 160, 6))
    points = np.column_stack([x.ravel(), y.ravel()]).astype(np.float32)
    mirror = np.array([[-1, 0, 255], [0, 1, 0], [0, 0, 1.0]])
    facts, _, reason = _quality(
        mirror,
        points,
        _project(points, mirror),
        reference_size=(256, 192),
        target_size=(256, 192),
        cfg=Config(),
    )
    assert reason == "reflection_rejected" and facts["reflection"] is True
    singular = np.diag([1.0, 0, 1])
    facts, _, reason = _quality(
        singular, points, points, reference_size=(256, 192), target_size=(256, 192), cfg=Config()
    )
    assert reason == "ill_conditioned_transform"
    json.dumps(facts, allow_nan=False)
    collinear = np.column_stack([np.arange(20, 220, 10), np.full(20, 80)]).astype(np.float32)
    _, _, reason = _quality(
        np.eye(3),
        collinear,
        collinear,
        reference_size=(256, 192),
        target_size=(256, 192),
        cfg=Config(),
    )
    assert reason == "spatially_degenerate_support"


def test_distinct_resolutions_and_detection_resizing_preserve_pixel_coordinates():
    reference = textured_scene(384, 512, seed=283)
    target = cv2.resize(reference, (256, 192), interpolation=cv2.INTER_AREA)
    result = register_pair(reference, target, config=Config(model="similarity", max_edge=192))
    assert result.status == "registered", result.quality
    expected = np.array([[0.5, 0, -0.25], [0, 0.5, -0.25], [0, 0, 1.0]])
    corners = np.array([[0, 0], [511, 0], [511, 383], [0, 383]], np.float64)
    assert (
        np.linalg.norm(_project(corners, expected) - _project(corners, result.matrix), axis=1).max()
        <= 1.5
    )


def test_pixel_budget_is_checked_before_opencv_allocations(monkeypatch):
    import photoproof.plugins.comparison.registration.algorithm as module

    def fail(*args, **kwargs):
        raise AssertionError("feature allocation happened before admission")

    monkeypatch.setattr(module, "_features", fail)
    huge = np.broadcast_to(np.array(0, np.uint8), (2049, 2048))
    with pytest.raises(InputRejectedError, match="allocation budget"):
        register_pair(huge, np.zeros((32, 32), np.uint8))


@pytest.mark.parametrize(
    "image",
    [np.zeros((0, 5), np.uint8), np.zeros((5, 5), np.float32), np.zeros((5, 5, 2), np.uint8)],
)
def test_invalid_working_pixels_are_rejected(image):
    with pytest.raises(InputRejectedError):
        register_pair(image, np.zeros((32, 32), np.uint8))


@pytest.mark.parametrize(
    "settings",
    [{"ratio": 1}, {"ratio": float("nan")}, {"max_features": 8001}, {"ecc_iterations": 101}],
)
def test_invalid_or_unbounded_config_is_rejected(settings):
    with pytest.raises(ValidationError):
        Config(**settings)


def test_flat_and_tiny_images_are_honest_registration_failures():
    for shape in [(4, 4), (64, 64)]:
        result = register_pair(np.zeros(shape, np.uint8), np.zeros(shape, np.uint8))
        assert result.status == "registration_failed"
        assert result.aligned_reference is None


def test_degenerate_homography_falls_back_only_to_a_verified_affine(monkeypatch):
    import photoproof.plugins.comparison.registration.algorithm as module

    reference = textured_scene(192, 256, seed=284)
    expected = np.array([[1, 0.02, 8], [-0.01, 1, 5], [0, 0, 1.0]])
    target = cv2.warpPerspective(reference, expected, (256, 192))
    original = module._fit

    def degenerate(rp, tp, *, model, cfg):
        return (
            np.diag([1.0, 0, 1])
            if model == "homography"
            else original(rp, tp, model=model, cfg=cfg)
        )

    monkeypatch.setattr(module, "_fit", degenerate)
    result = register_pair(reference, target)
    assert result.status == "registered" and result.model == "affine"
    assert result.flags == ("affine_fallback_from_homography",)
    corners = np.array([[0, 0], [255, 0], [255, 191], [0, 191]], np.float64)
    assert (
        np.linalg.norm(_project(corners, result.matrix) - _project(corners, expected), axis=1).max()
        < 1.5
    )


def test_no_estimator_result_never_warps_unaligned_pixels(monkeypatch):
    import photoproof.plugins.comparison.registration.algorithm as module

    monkeypatch.setattr(module, "_fit", lambda *args, **kwargs: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("unaligned fallback must never run")

    monkeypatch.setattr(module, "_warp_reference", forbidden)
    reference = textured_scene(192, 256, seed=285)
    target = cv2.warpPerspective(
        reference, np.array([[1, 0, 8], [0, 1, 5], [0, 0, 1.0]]), (256, 192)
    )
    result = register_pair(reference, target)
    assert result.status == "registration_failed"
    assert result.quality["reason"] == "estimator_failed"
    assert result.aligned_reference is None


def test_readonly_grayscale_reference_and_rgb_target_remain_distinguishable():
    reference = cv2.cvtColor(textured_scene(192, 256, seed=286), cv2.COLOR_RGB2GRAY)
    matrix = np.array([[1, 0, 8], [0, 1, 5], [0, 0, 1.0]])
    target_gray = cv2.warpPerspective(reference, matrix, (256, 192))
    target = np.repeat(target_gray[..., None], 3, axis=2)
    before_r, before_t = reference.copy(), target.copy()
    reference.setflags(write=False)
    target.setflags(write=False)
    result = register_pair(reference, target, config=Config(model="affine"))
    assert result.status == "registered", result.quality
    assert result.aligned_reference.ndim == 2 and target.ndim == 3
    assert np.array_equal(reference, before_r) and np.array_equal(target, before_t)
    corners = np.array([[0, 0], [255, 0], [255, 191], [0, 191]], np.float64)
    assert (
        np.linalg.norm(_project(corners, result.matrix) - _project(corners, matrix), axis=1).max()
        < 1.5
    )

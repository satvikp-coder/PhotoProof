"""Dense masks use actual transformed fixture pixels and RANSAC-selected rows."""

import numpy as np
import pytest

from photoproof.contracts.copymove import Cluster, Clusters, Keypoints, Matches, Verified
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.mask.localization import localize_verified
from photoproof.plugins.copymove.mask.schema import Config
from photoproof.plugins.copymove.verification.algorithm import verify_clusters
from tests.fixtures.synthetic import copy_move, textured_scene


def fixture_models(*, angle=0, scale=1, textured_only=False):
    original = (
        np.random.default_rng(24).integers(0, 256, (512, 640, 3), np.uint8)
        if textured_only
        else textured_scene(512, 640, seed=24)
    )
    fixture = copy_move(original, translate=(0, 240), rotate_deg=angle, scale=scale)
    sy, sx, ey, ex = fixture.meta["source_bbox"]
    ty, tx, by, bx = fixture.meta["target_bbox"]
    radians = np.deg2rad(angle)
    linear = np.array([[np.cos(radians), np.sin(radians)], [-np.sin(radians), np.cos(radians)]])
    # Pillow rotates around patch center, then resizes using pixel-center coordinates.
    linear *= scale
    offset = np.array([(tx + bx - 1) / 2, (ty + by - 1) / 2]) - linear @ np.array(
        [(sx + ex - 1) / 2, (sy + ey - 1) / 2]
    )
    xx, yy = np.meshgrid(np.linspace(sx + 10, ex - 11, 5), np.linspace(sy + 10, ey - 11, 5))
    source = np.column_stack((xx.ravel(), yy.ravel()))
    target = source @ linear.T + offset
    xy = np.vstack((source, target)).astype(np.float32)
    features = Keypoints(
        source_sha256="a" * 64,
        image_size=(640, 512),
        xy=xy,
        scale=np.ones(50, np.float32),
        angle=np.zeros(50, np.float32),
        response=np.ones(50, np.float32),
        desc=np.zeros((50, 128), np.float32),
    )
    matches = Matches(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        num_keypoints=50,
        pairs=np.column_stack((np.arange(25), np.arange(25, 50))),
        distance=np.zeros(25, np.float32),
    )
    candidates = Clusters(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        num_matches=25,
        clusters=(
            Cluster(
                id=0,
                pair_indices=tuple(range(25)),
                source_bbox=(sy, sx, ey, ex),
                target_bbox=(ty, tx, by, bx),
                mean_displacement=(0.0, 240.0),
            ),
        ),
    )
    outcome = verify_clusters(features, matches, candidates, image=fixture.image, seed=24)
    assert len(outcome.verified.transforms) == 1
    return fixture, features, matches, outcome


@pytest.mark.parametrize("angle,scale", [(0, 1), (30, 1), (0, 0.8)])
def test_fixture_dense_mask_baselines(angle, scale):
    fixture, features, matches, verification = fixture_models(angle=angle, scale=scale)
    before = fixture.image.tobytes()
    result = localize_verified(
        features,
        matches,
        verification.verified,
        verification.inliers,
        image=fixture.image,
        reproj_threshold=3.0,
        min_inliers=6,
    )
    predicted, expected = result.mask.mask > 0, fixture.mask > 0
    iou = np.count_nonzero(predicted & expected) / np.count_nonzero(predicted | expected)
    print(f"dense mask default baseline rotation={angle},scale={scale},union_iou={iou:.6f}")
    assert iou > 0
    assert np.any(result.mask.mask == 1) and np.any(result.mask.mask == 2)
    assert result.summary["status"] == "copy_move_regions_found"
    assert fixture.image.tobytes() == before
    assert result.mask.calibration == "uncalibrated"
    assert not result.correlation.valid.flags.writeable
    assert not result.correlation.values.flags.writeable


def test_empty_verified_is_method_negative_not_authenticity():
    fixture, features, matches, verification = fixture_models()
    empty = Verified(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        transforms=(),
        cluster_ids=verification.verified.cluster_ids,
    )
    inliers = Clusters(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        clusters=(),
        num_matches=25,
    )
    result = localize_verified(
        features, matches, empty, inliers, image=fixture.image, reproj_threshold=3.0, min_inliers=6
    )
    assert not result.mask.mask.any() and not result.correlation.valid.any()
    assert result.summary["status"] == "no_copy_move_found"
    assert "authentic" not in str(result.summary) and "verdict" not in result.summary


@pytest.mark.parametrize("config", [Config(max_pixels=1), Config(max_pixel_work=1)])
def test_resource_budget_rejects_without_partial_result(config):
    fixture, features, matches, verification = fixture_models()
    with pytest.raises(InputRejectedError, match="budget"):
        localize_verified(
            features,
            matches,
            verification.verified,
            verification.inliers,
            image=fixture.image,
            reproj_threshold=3.0,
            min_inliers=6,
            config=config,
        )


def test_clean_textured_translation_reaches_required_iou_without_tuning():
    fixture, features, matches, verification = fixture_models(textured_only=True)
    result = localize_verified(
        features,
        matches,
        verification.verified,
        verification.inliers,
        image=fixture.image,
        reproj_threshold=3.0,
        min_inliers=6,
    )
    predicted, expected = result.mask.mask > 0, fixture.mask > 0
    iou = np.count_nonzero(predicted & expected) / np.count_nonzero(predicted | expected)
    print(f"clean textured translation default union_iou={iou:.6f}")
    assert iou >= 0.7


def test_transparent_pixels_never_become_visible_copy_evidence():
    fixture, features, matches, verification = fixture_models(textured_only=True)
    alpha = np.full(fixture.image.shape[:2], 255, np.uint8)
    alpha[130:180, 165:215] = 0
    rgba = np.dstack((fixture.image, alpha))
    result = localize_verified(
        features,
        matches,
        verification.verified,
        verification.inliers,
        image=rgba,
        reproj_threshold=3.0,
        min_inliers=6,
    )
    assert not result.mask.mask[130:180, 165:215].any()
    assert not result.correlation.valid[130:180, 165:215].any()
    assert not result.mask.mask[130:180, 405:455].any()
    assert not result.correlation.valid[130:180, 405:455].any()


def test_flat_pixels_with_verified_geometry_are_still_unavailable():
    fixture, features, matches, verification = fixture_models()
    result = localize_verified(
        features,
        matches,
        verification.verified,
        verification.inliers,
        image=np.full_like(fixture.image, 120),
        reproj_threshold=3.0,
        min_inliers=6,
    )
    assert not result.mask.mask.any() and not result.correlation.valid.any()
    assert result.summary["status"] == "no_copy_move_found"


def test_small_displacement_is_excluded_without_lowering_default():
    fixture, features, matches, verification = fixture_models()
    result = localize_verified(
        features,
        matches,
        verification.verified,
        verification.inliers,
        image=fixture.image,
        reproj_threshold=3.0,
        min_inliers=6,
        config=Config(min_displacement_px=300.0),
    )
    assert not result.mask.mask.any() and not result.correlation.valid.any()

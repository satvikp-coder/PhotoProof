"""Known geometry, actual inlier row identity and conservative failure semantics."""

import json

import numpy as np
import pytest
from pydantic import ValidationError

from photoproof.contracts.copymove import Cluster, Clusters, Keypoints, Matches, Verified
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.verification import algorithm
from photoproof.plugins.copymove.verification.schema import Config
from tests.fixtures.synthetic import textured_scene

SOURCE = "a" * 64


def _bounds(points):
    return (
        int(np.floor(points[:, 1].min())),
        int(np.floor(points[:, 0].min())),
        int(np.ceil(points[:, 1].max())) + 1,
        int(np.ceil(points[:, 0].max())) + 1,
    )


def _models(source, target):
    xy = np.vstack((source, target)).astype(np.float32)
    n = len(source)
    features = Keypoints(
        source_sha256=SOURCE,
        image_size=(512, 512),
        xy=xy,
        scale=np.ones(len(xy), np.float32),
        angle=np.zeros(len(xy), np.float32),
        response=np.ones(len(xy), np.float32),
        desc=np.zeros((len(xy), 128), np.float32),
    )
    matches = Matches(
        source_sha256=SOURCE,
        image_size=(512, 512),
        num_keypoints=len(xy),
        pairs=np.column_stack((np.arange(n), np.arange(n, 2 * n))).astype(np.int32),
        distance=np.zeros(n, np.float32),
    )
    clusters = Clusters(
        source_sha256=SOURCE,
        image_size=(512, 512),
        num_matches=n,
        clusters=(
            Cluster(
                id=7,
                pair_indices=tuple(range(n)),
                source_bbox=_bounds(source),
                target_bbox=_bounds(target),
                mean_displacement=tuple((target - source).mean(axis=0)[::-1]),
            ),
        )
        if n
        else (),
    )
    return features, matches, clusters


def _fixture(*, angle=0, scale=1, outliers=0):
    rng = np.random.default_rng(23)
    source = rng.uniform(20, 120, (24 + outliers, 2))
    radians = np.deg2rad(angle)
    linear = scale * np.array(
        [[np.cos(radians), -np.sin(radians)], [np.sin(radians), np.cos(radians)]]
    )
    target = (source - 70) @ linear.T + [294, 150]
    target[:24] += rng.normal(0, 0.1, (24, 2))
    if outliers:
        target[24:] = rng.uniform([390, 300], [480, 450], (outliers, 2))
    return (*_models(source, target), textured_scene(512, 512, seed=23))


@pytest.mark.parametrize("angle", [0, 30, 60, 90])
@pytest.mark.parametrize("scale", [0.7, 1.0, 1.3])
def test_seeded_similarity_recovers_parameters_and_original_row_identities(angle, scale):
    features, matches, clusters, image = _fixture(angle=angle, scale=scale)
    result = algorithm.verify_clusters(features, matches, clusters, image=image, seed=23)
    assert len(result.verified.transforms) == 1
    transform = result.verified.transforms[0]
    assert abs(transform.rotation_deg - angle) <= 2
    assert abs(transform.scale / scale - 1) <= 0.03
    assert transform.inliers == 24 and transform.rmse <= 0.5
    assert result.inliers.clusters[0].pair_indices == tuple(range(24))
    assert result.inliers.num_matches == len(matches.pairs)
    assert not transform.matrix.flags.writeable
    assert 0 <= transform.score <= 1 and result.verified.calibration == "uncalibrated"
    assert result.details[0]["inlier_ratio"] == 1
    assert '"probability"' not in json.dumps(result.details)
    restored = Verified.from_npz(result.verified.to_npz(), source_sha256=SOURCE)
    np.testing.assert_array_equal(restored.transforms[0].matrix, transform.matrix)


def test_outliers_are_excluded_with_actual_parent_indices_not_just_an_inlier_count():
    features, matches, clusters, image = _fixture(outliers=20)
    original_pixels, original_points = image.tobytes(), features.xy.tobytes()
    result = algorithm.verify_clusters(features, matches, clusters, image=image, seed=23)
    assert len(result.verified.transforms) == 1
    assert result.verified.transforms[0].inliers == 24
    assert result.inliers.clusters[0].pair_indices == tuple(range(24))
    assert result.details[0]["inlier_ratio"] == 24 / 44
    assert result.verified.transforms[0].score < 0.6
    assert image.tobytes() == original_pixels and features.xy.tobytes() == original_points
    repeated = algorithm.verify_clusters(features, matches, clusters, image=image, seed=23)
    assert repeated.verified.to_npz() == result.verified.to_npz()
    assert repeated.inliers.to_json() == result.inliers.to_json()


def test_random_correspondences_reject_with_a_reason_and_no_zero_score_transform():
    rng = np.random.default_rng(230)
    source = rng.uniform(20, 120, (30, 2))
    target = rng.uniform([200, 100], [480, 450], (30, 2))
    features, matches, clusters = _models(source, target)
    result = algorithm.verify_clusters(
        features, matches, clusters, image=textured_scene(512, 512, seed=23), seed=23
    )
    assert result.verified.transforms == () and result.inliers.clusters == ()
    assert result.details[0]["reason"] in {"too_few_inliers", "degenerate", "model_not_found"}
    assert result.details[0]["score"] is None
    assert result.summary["max_heuristic_score"] is None


def test_collinear_points_reject_before_native_estimation(monkeypatch):
    source = np.column_stack((np.arange(20, 120, 10), np.full(10, 40)))
    features, matches, clusters = _models(source, source + np.array([224, 80]))

    def forbidden(*args, **kwargs):
        pytest.fail("collinear geometry must be rejected before native estimation")

    monkeypatch.setattr(algorithm.cv2, "estimateAffinePartial2D", forbidden)
    result = algorithm.verify_clusters(
        features, matches, clusters, image=textured_scene(512, 512, seed=23), seed=23
    )
    assert result.verified.transforms == ()
    assert result.details[0]["reason"] == "degenerate"


def test_repeated_coordinates_do_not_inflate_minimum_inlier_support():
    source = np.repeat(np.array([[20, 20], [40, 20], [20, 40]], np.float64), 2, axis=0)
    features, matches, clusters = _models(source, source + np.array([224, 80]))
    result = algorithm.verify_clusters(
        features, matches, clusters, image=textured_scene(512, 512, seed=23), seed=23
    )
    assert result.verified.transforms == ()
    assert result.details[0]["reason"] == "too_few_inliers"


def test_extreme_scale_and_shear_are_rejected_and_configuration_is_honored():
    features, matches, clusters, image = _fixture(scale=3)
    result = algorithm.verify_clusters(features, matches, clusters, image=image, seed=23)
    assert result.details[0]["detail"] == "scale_out_of_range"
    source = np.random.default_rng(23).uniform(20, 120, (24, 2))
    target = source @ np.array([[1, 0], [1.2, 1]]) + [180, 80]
    features, matches, clusters = _models(source, target)
    rejected = algorithm.verify_clusters(
        features, matches, clusters, image=image, seed=23, config=Config(model="affine")
    )
    assert rejected.details[0]["detail"] == "excessive_shear"
    accepted = algorithm.verify_clusters(
        features,
        matches,
        clusters,
        image=image,
        seed=23,
        config=Config(model="affine", max_shear_deg=60),
    )
    assert len(accepted.verified.transforms) == 1


def test_periodic_pixels_flag_translation_and_cap_geometric_score():
    source = np.random.default_rng(23).uniform(20, 120, (24, 2))
    features, matches, clusters = _models(source, source + np.array([224, 80]))
    yy, xx = np.indices((512, 512))
    image = (((xx // 8 + yy // 8) % 2) * 255).astype(np.uint8)
    image.flags.writeable = False
    original = image.tobytes()
    result = algorithm.verify_clusters(features, matches, clusters, image=image, seed=23)
    assert "possible_repetitive_structure" in result.verified.transforms[0].flags
    assert result.verified.transforms[0].score <= 0.25
    assert result.summary["repetitive_structure_flags"] == 1
    assert image.tobytes() == original


def test_work_budget_rejects_before_native_estimation(monkeypatch):
    features, matches, clusters, image = _fixture()

    def forbidden(*args, **kwargs):
        pytest.fail("native RANSAC must not start above its work limit")

    monkeypatch.setattr(algorithm.cv2, "estimateAffinePartial2D", forbidden)
    with pytest.raises(InputRejectedError, match="RANSAC work limit"):
        algorithm.verify_clusters(
            features, matches, clusters, image=image, seed=23, config=Config(max_ransac_work=1)
        )


def test_empty_clusters_produce_empty_typed_diagnostics_not_a_verdict():
    features, matches, clusters = _models(np.empty((0, 2)), np.empty((0, 2)))
    result = algorithm.verify_clusters(
        features, matches, clusters, image=np.zeros((512, 512, 3), np.uint8), seed=23
    )
    assert result.verified.transforms == () and result.inliers.clusters == ()
    assert result.summary["max_heuristic_score"] is None
    assert "verdict" not in result.summary


def test_verified_contract_rejects_duplicate_cluster_ids():
    features, matches, clusters, image = _fixture()
    result = algorithm.verify_clusters(features, matches, clusters, image=image, seed=23)
    with pytest.raises(InputRejectedError, match="repeat a cluster id"):
        Verified(**{**result.verified.model_dump(), "transforms": result.verified.transforms * 2})


def test_scale_range_alias_and_invalid_config_are_explicit():
    config = Config.model_validate({"scale_range": [0.7, 1.3]})
    assert config.scale_min == 0.7 and config.scale_max == 1.3
    for values in [
        {"scale_min": 2, "scale_max": 1},
        {"scale_range": [0.5, 2], "scale_min": 0.5},
        {"reproj_threshold": float("nan")},
        {"max_iterations": True},
        {"ransac_stop_confidence": 1},
        {"periodic_max_edge": 257},
    ]:
        with pytest.raises(ValidationError):
            Config.model_validate(values)


def test_actual_native_rng_uses_the_supplied_seed(monkeypatch):
    original = algorithm.cv2.setRNGSeed
    seeds = []

    def record(seed):
        seeds.append(seed)
        original(seed)

    monkeypatch.setattr(algorithm.cv2, "setRNGSeed", record)
    features, matches, clusters, image = _fixture()
    result = algorithm.verify_clusters(features, matches, clusters, image=image, seed=1729)
    assert seeds == [(1729 + 7 * 2654435761) & 0x7FFFFFFF]
    assert result.summary["seed"] == 1729 and result.details[0]["native_seed"] == seeds[0]


@pytest.mark.parametrize(
    "field,value", [("source_sha256", "b" * 64), ("image_size", (513, 512)), ("num_matches", 99)]
)
def test_parent_artifact_semantics_must_agree(field, value):
    features, matches, clusters, image = _fixture()
    with pytest.raises(InputRejectedError, match="source, frame or parent counts"):
        algorithm.verify_clusters(
            features, matches, clusters.model_copy(update={field: value}), image=image, seed=23
        )


def test_invalid_native_mask_cannot_become_verified_inliers(monkeypatch):
    features, matches, clusters, image = _fixture()

    def corrupt(*args, **kwargs):
        return np.array([[1, 0, 224], [0, 1, 80]], np.float64), np.full((24, 1), 2, np.uint8)

    monkeypatch.setattr(algorithm.cv2, "estimateAffinePartial2D", corrupt)
    with pytest.raises(InputRejectedError, match="inlier dimensions"):
        algorithm.verify_clusters(features, matches, clusters, image=image, seed=23)

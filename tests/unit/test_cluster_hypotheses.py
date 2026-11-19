"""Candidate grouping against known geometry, with explicit resource guards."""

import numpy as np
import pytest

from photoproof.contracts.copymove import Keypoints, Matches
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.clustering import hypotheses

SOURCE = "a" * 64


def _features(xy):
    return Keypoints(
        source_sha256=SOURCE,
        image_size=(512, 512),
        xy=np.asarray(xy, np.float32),
        scale=np.ones(len(xy), np.float32),
        angle=np.zeros(len(xy), np.float32),
        response=np.ones(len(xy), np.float32),
        desc=np.ones((len(xy), 128), np.float32),
    )


def _matches(features, pairs):
    return Matches(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        num_keypoints=len(features.xy),
        pairs=np.asarray(pairs, np.int32).reshape(-1, 2),
        distance=np.zeros(len(pairs), np.float32),
    )


def _case(*, angle=0, scale=1):
    xx, yy = np.meshgrid(np.arange(20, 69, 16), np.arange(20, 69, 16))
    source = np.column_stack((xx.ravel(), yy.ravel())).astype(np.float64)
    radians = np.deg2rad(angle)
    matrix = np.array([[np.cos(radians), -np.sin(radians)], [np.sin(radians), np.cos(radians)]])
    target = (source - 44) @ matrix.T * scale + 44 + [180, 120]
    features = _features(np.vstack((source, target)))
    matches = _matches(features, np.column_stack((np.arange(16), np.arange(16, 32))))
    box = (
        int(np.floor(target[:, 1].min())),
        int(np.floor(target[:, 0].min())),
        int(np.ceil(target[:, 1].max())) + 1,
        int(np.ceil(target[:, 0].max())) + 1,
    )
    return features, matches, (20, 20, 69, 69), box


def _iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)


@pytest.mark.parametrize("angle,scale", [(0, 1), (30, 1), (0, 0.8)])
def test_planted_transform_correspondences_have_one_dominant_cluster(angle, scale):
    features, matches, source, target = _case(angle=angle, scale=scale)
    clusters, stats = hypotheses.cluster_matches(features, matches)
    assert len(clusters.clusters) == 1
    cluster = clusters.clusters[0]
    assert _iou(cluster.source_bbox, source) >= 0.5
    assert _iou(cluster.target_bbox, target) >= 0.5
    assert cluster.pair_indices == tuple(range(16))
    assert stats["method_used"] == "hierarchical"
    assert clusters.calibration == "uncalibrated"
    assert "score" not in clusters.to_json()
    repeated, repeated_stats = hypotheses.cluster_matches(features, matches)
    assert repeated.to_json() == clusters.to_json() and repeated_stats == stats


def test_two_independent_regions_remain_two_hypotheses():
    features, matches, _, _ = _case()
    source = features.xy[:16] + 280
    target = source + np.array([120, 100])
    combined = _features(np.vstack((features.xy, source, target)))
    pairs = np.vstack((matches.pairs, np.column_stack((np.arange(32, 48), np.arange(48, 64)))))
    result, _ = hypotheses.cluster_matches(combined, _matches(combined, pairs))
    assert len(result.clusters) == 2
    assert {c.pair_indices for c in result.clusters} == {tuple(range(16)), tuple(range(16, 32))}


def test_seeded_random_matches_do_not_form_minimum_sized_clusters():
    features = _features(np.random.default_rng(22).uniform(0, 511, (24, 2)))
    matches = _matches(features, np.arange(24).reshape(-1, 2))
    result, stats = hypotheses.cluster_matches(features, matches)
    assert result.clusters == ()
    assert stats["num_rejected_incoherent"] > 0


def test_direction_and_symmetric_duplicates_preserve_original_parent_indices():
    features, matches, _, _ = _case()
    reversed_pairs = np.vstack((matches.pairs[:, ::-1], matches.pairs))
    result, stats = hypotheses.cluster_matches(features, _matches(features, reversed_pairs))
    assert len(result.clusters) == 1 and result.num_matches == 32
    assert result.clusters[0].pair_indices == tuple(range(16))
    assert result.clusters[0].mean_displacement == (120, 180)
    assert stats["num_unique_pairs"] == 16


def test_ward_uses_ward_distance_not_greedy_centroid_distance():
    points = np.array([[0.0], [0.1], [0.25]])
    groups = hypotheses._ward(points, threshold=0.22, minimum=1)
    assert [g.tolist() for g in groups] == [[0, 1], [2]]


def test_large_ward_input_falls_back_before_quadratic_allocation(monkeypatch):
    features, matches, _, _ = _case()

    def forbidden(*args, **kwargs):
        pytest.fail("quadratic Ward must not run above its input bound")

    monkeypatch.setattr(hypotheses, "_ward", forbidden)
    result, stats = hypotheses.cluster_matches(features, matches, ward_limit=8)
    assert len(result.clusters) == 1
    assert stats["method_used"] == "dbscan" and stats["fallback_reason"] == "ward_input_limit"


def test_density_duplicate_compression_and_border_reachability():
    points = np.array([[0, 0], [0, 0], [0.1, 0], [0.2, 0], [0.4, 0], [10, 10]], np.float64)
    groups, work = hypotheses._density(points, eps=0.21, minimum=4, max_work=100)
    assert [g.tolist() for g in groups] == [[0, 1, 2, 3, 4]]
    assert work <= 25  # Duplicate rows carry weight without duplicate neighborhood queries.


def test_density_work_limit_fails_instead_of_returning_a_partial_or_empty_result():
    points = np.column_stack((np.arange(10, dtype=np.float64) / 100, np.zeros(10)))
    with pytest.raises(InputRejectedError, match="work limit"):
        hypotheses._density(points, eps=1, minimum=4, max_work=5)


def test_empty_matches_are_empty_hypotheses_without_a_verdict():
    features = _features(np.empty((0, 2)))
    result, stats = hypotheses.cluster_matches(features, _matches(features, []))
    assert result.clusters == () and stats["num_clusters"] == 0
    assert "verdict" not in result.to_json()


@pytest.mark.parametrize(
    "field,value", [("source_sha256", "b" * 64), ("image_size", (513, 512)), ("num_keypoints", 33)]
)
def test_parent_source_frame_and_count_must_agree(field, value):
    features, matches, _, _ = _case()
    with pytest.raises(InputRejectedError, match="source, frame or count"):
        hypotheses.cluster_matches(features, matches.model_copy(update={field: value}))


def test_pair_limit_rejects_before_ward(monkeypatch):
    features, matches, _, _ = _case()
    with pytest.raises(InputRejectedError, match="pair limit"):
        hypotheses.cluster_matches(features, matches, max_input_pairs=8)


@pytest.mark.parametrize(
    "config",
    [
        {"method": "unknown"},
        {"method": []},
        {"cut_threshold": float("nan")},
        {"cut_threshold": "0.5"},
        {"cut_threshold": True},
        {"eps": 0},
        {"eps": float("inf")},
        {"eps": "0.5"},
        {"coherence_rmse_px": float("nan")},
        {"coherence_rmse_px": False},
        {"merge_gap_px": float("nan")},
        {"merge_gap_px": True},
        {"merge_gap_px": 257},
        {"ward_limit": 1025},
        {"max_input_pairs": 0},
        {"max_neighbor_work": True},
        {"min_matches_per_cluster": 4.5},
    ],
)
def test_invalid_native_clustering_parameters_fail_explicitly(config):
    features, matches, _, _ = _case()
    with pytest.raises(InputRejectedError):
        hypotheses.cluster_matches(features, matches, **config)


def _fragments(*, distant=False, inconsistent=False):
    features, matches, _, _ = _case()
    source = features.xy[matches.pairs[:, 0]].astype(np.float64)
    target = features.xy[matches.pairs[:, 1]].astype(np.float64)
    if distant:
        source[8:] += 150
        target[8:] += 150
    if inconsistent:
        target[8:, 0] += 40
    scale = np.array([512, 512], np.float64)
    groups = []
    for indices in (np.arange(8), np.arange(8, 16)):
        matrix = hypotheses._coherence_transform(
            source[indices], target[indices], scale=scale, rmse=3
        )
        assert matrix is not None
        groups.append((indices, matrix))
    return groups, source, target, scale


def test_adjacent_fragments_merge_only_with_a_coherent_combined_fit():
    groups, source, target, scale = _fragments()
    merged, work = hypotheses._merge_neighbors(
        groups, source, target, scale=scale, rmse=3, gap=16, max_work=100
    )
    assert len(merged) == 1 and work == 16
    np.testing.assert_array_equal(merged[0][0], np.arange(16))
    groups, source, target, scale = _fragments(inconsistent=True)
    rejected, _ = hypotheses._merge_neighbors(
        groups, source, target, scale=scale, rmse=3, gap=16, max_work=100
    )
    assert len(rejected) == 2


def test_distant_regions_with_identical_motion_do_not_merge():
    groups, source, target, scale = _fragments(distant=True)
    result, work = hypotheses._merge_neighbors(
        groups, source, target, scale=scale, rmse=3, gap=16, max_work=100
    )
    assert len(result) == 2 and work == 0


def test_merge_and_coherence_budgets_raise_instead_of_dropping_fragments():
    groups, source, target, scale = _fragments()
    with pytest.raises(InputRejectedError, match="merge work limit"):
        hypotheses._merge_neighbors(
            groups, source, target, scale=scale, rmse=3, gap=16, max_work=15
        )
    features, matches, _, _ = _case()
    with pytest.raises(InputRejectedError, match="coherence work limit"):
        hypotheses.cluster_matches(features, matches, max_neighbor_work=1)


def test_affine_envelope_projection_clamps_to_working_frame():
    source = np.array([[0, 0], [100, 0], [0, 100]], np.float64)
    scale = np.array([512, 512], np.float64)
    matrix = np.array([[1, 1], [1, -1], [0.8, 0.4]])
    target = (np.column_stack((source / scale, np.ones(3))) @ matrix) * scale
    source_box, target_box = hypotheses._boxes(source, target, matrix, scale=scale)
    assert source_box == (0, 0, 101, 101)
    assert target_box[3] == 512 and 0 <= target_box[0] < target_box[2] <= 512
    assert 0 <= target_box[1] < target_box[3]

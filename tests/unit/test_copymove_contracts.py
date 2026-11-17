"""Tests for the copy-move chain data contracts."""

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.contracts import (
    Cluster,
    Clusters,
    Keypoints,
    Mask,
    Matches,
    Region,
    Verified,
    VerifiedTransform,
)


def _keypoints(n: int = 4) -> Keypoints:
    return Keypoints(
        xy=np.ascontiguousarray(np.zeros((n, 2), dtype=np.float32)),
        scale=np.full(n, 1.0, dtype=np.float32),
        angle=np.zeros(n, dtype=np.float32),
        response=np.full(n, 10.0, dtype=np.float32),
        desc=np.ascontiguousarray(np.zeros((n, 128), dtype=np.float32)),
    )


def _matches(m: int = 3) -> Matches:
    return Matches(
        pairs=np.ascontiguousarray(np.zeros((m, 2), dtype=np.int32)),
        distance=np.full(m, 1.0, dtype=np.float32),
    )


def _keypoints_equal(a: Keypoints, b: Keypoints) -> bool:
    return (
        a.contract_version == b.contract_version
        and np.array_equal(a.xy, b.xy)
        and np.array_equal(a.scale, b.scale)
        and np.array_equal(a.angle, b.angle)
        and np.array_equal(a.response, b.response)
        and np.array_equal(a.desc, b.desc)
    )


def _matches_equal(a: Matches, b: Matches) -> bool:
    return (
        a.contract_version == b.contract_version
        and np.array_equal(a.pairs, b.pairs)
        and np.array_equal(a.distance, b.distance)
    )


def _verified_equal(a: VerifiedTransform, b: VerifiedTransform) -> bool:
    return (
        a.cluster_id == b.cluster_id
        and np.array_equal(a.matrix, b.matrix)
        and a.inliers == b.inliers
        and a.rmse == b.rmse
        and a.scale == b.scale
        and a.rotation_deg == b.rotation_deg
        and a.score == b.score
        and a.flags == b.flags
    )


def _mask_equal(a: Mask, b: Mask) -> bool:
    return (
        a.contract_version == b.contract_version
        and np.array_equal(a.mask, b.mask)
        and a.regions == b.regions
    )


def test_keypoints_round_trip() -> None:
    kp = _keypoints(5)
    data = kp.to_npz()
    kp2 = Keypoints.from_npz(data)
    assert _keypoints_equal(kp2, kp)


def test_keypoints_rejects_bad_shapes() -> None:
    with pytest.raises(InputRejectedError):
        Keypoints(
            xy=np.zeros((3, 3), dtype=np.float32),
            scale=np.zeros(3, dtype=np.float32),
            angle=np.zeros(3, dtype=np.float32),
            response=np.zeros(3, dtype=np.float32),
            desc=np.zeros((3, 64), dtype=np.float32),
        )


def test_keypoints_rejects_mismatched_lengths() -> None:
    with pytest.raises(InputRejectedError):
        Keypoints(
            xy=np.zeros((4, 2), dtype=np.float32),
            scale=np.zeros(5, dtype=np.float32),
            angle=np.zeros(4, dtype=np.float32),
            response=np.zeros(4, dtype=np.float32),
            desc=np.zeros((4, 128), dtype=np.float32),
        )


def test_matches_round_trip() -> None:
    mt = _matches(4)
    data = mt.to_npz()
    mt2 = Matches.from_npz(data)
    assert _matches_equal(mt2, mt)


def test_matches_rejects_bad_shapes() -> None:
    with pytest.raises(InputRejectedError):
        Matches(
            pairs=np.zeros((3, 3), dtype=np.int32),
            distance=np.zeros(3, dtype=np.float32),
        )


def test_clusters_round_trip() -> None:
    cl = Cluster(
        id=1,
        pair_indices=(0, 2, 5),
        source_bbox=(10, 10, 50, 50),
        target_bbox=(60, 70, 100, 110),
        mean_displacement=(50.0, 60.0),
    )
    clusters = Clusters(contract_version="1.0.0", clusters=(cl,))
    text = clusters.to_json()
    clusters2 = Clusters.from_json(text)
    assert clusters2 == clusters
    assert clusters2.clusters[0].id == 1


def test_verified_round_trip() -> None:
    matrix = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, 10.0]], dtype=np.float64)
    vt = VerifiedTransform(
        cluster_id=0,
        matrix=matrix,
        inliers=10,
        rmse=1.0,
        scale=1.0,
        rotation_deg=0.0,
        score=0.9,
        flags=("ransac_verified",),
    )
    verified = Verified(contract_version="1.0.0", transforms=(vt,))
    data = verified.to_npz()
    verified2 = Verified.from_npz(data)
    assert len(verified2.transforms) == 1
    assert _verified_equal(verified2.transforms[0], vt)


def test_mask_round_trip() -> None:
    mask = np.ascontiguousarray(
        np.array([[0, 0, 1, 1], [0, 0, 1, 1], [2, 2, 0, 0], [2, 2, 0, 0]], dtype=np.uint8)
    )
    regions = (
        Region(kind="source", bbox=(0, 2, 2, 4), area=4),
        Region(kind="target", bbox=(2, 0, 4, 2), area=4),
    )
    m = Mask(contract_version="1.0.0", mask=mask, regions=regions)
    data = m.to_npz()
    m2 = Mask.from_npz(data)
    assert _mask_equal(m2, m)


def test_mask_rejects_invalid_values() -> None:
    mask = np.ascontiguousarray(np.array([[0, 3], [0, 0]], dtype=np.uint8))
    with pytest.raises(InputRejectedError):
        Mask(contract_version="1.0.0", mask=mask, regions=())


def test_golden_chain_round_trip() -> None:
    """Build a minimal copy-move contract chain and verify all round-trips."""
    # Keypoints: 4 keypoints on a source region
    kp = Keypoints(
        xy=np.ascontiguousarray(
            np.array([[10, 10], [10, 20], [20, 10], [20, 20]], dtype=np.float32)
        ),
        scale=np.full(4, 1.0, dtype=np.float32),
        angle=np.zeros(4, dtype=np.float32),
        response=np.full(4, 100.0, dtype=np.float32),
        desc=np.ascontiguousarray(
            np.random.default_rng(0).normal(0, 1, size=(4, 128)).astype(np.float32)
        ),
    )
    assert _keypoints_equal(Keypoints.from_npz(kp.to_npz()), kp)

    # Matches: each source keypoint matches a target keypoint
    mt = Matches(
        pairs=np.ascontiguousarray(np.array([[0, 1], [1, 2], [2, 3], [3, 0]], dtype=np.int32)),
        distance=np.full(4, 50.0, dtype=np.float32),
    )
    assert _matches_equal(Matches.from_npz(mt.to_npz()), mt)

    # Clusters: one cluster covering all matches
    cl = Cluster(
        id=0,
        pair_indices=(0, 1, 2, 3),
        source_bbox=(10, 10, 21, 21),
        target_bbox=(40, 50, 51, 61),
        mean_displacement=(30.0, 40.0),
    )
    clusters = Clusters(contract_version="1.0.0", clusters=(cl,))
    assert Clusters.from_json(clusters.to_json()) == clusters

    # Verified: one RANSAC-verified transform
    matrix = np.array([[1.0, 0.0, 40.0], [0.0, 1.0, 30.0]], dtype=np.float64)
    vt = VerifiedTransform(
        cluster_id=0,
        matrix=matrix,
        inliers=4,
        rmse=0.5,
        scale=1.0,
        rotation_deg=0.0,
        score=0.95,
        flags=("ransac_verified",),
    )
    verified = Verified(contract_version="1.0.0", transforms=(vt,))
    vf2 = Verified.from_npz(verified.to_npz())
    assert _verified_equal(vf2.transforms[0], vt)

    # Mask: source and target regions
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[10:21, 10:21] = 1  # source
    mask[40:51, 50:61] = 2  # target
    regions = (
        Region(kind="source", bbox=(10, 10, 21, 21), area=121),
        Region(kind="target", bbox=(40, 50, 51, 61), area=121),
    )
    m = Mask(contract_version="1.0.0", mask=mask, regions=regions)
    assert _mask_equal(Mask.from_npz(m.to_npz()), m)
    assert set(np.unique(mask).tolist()) <= {0, 1, 2}

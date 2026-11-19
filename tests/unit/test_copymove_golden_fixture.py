"""Golden artifacts describe one actual seeded synthetic image consistently."""

import hashlib

import numpy as np

from photoproof.contracts.copymove import Clusters, Keypoints, Mask, Matches, Verified
from tests.fixtures.copymove_golden import golden_examples


def test_golden_chain_is_deterministic_source_bound_and_round_trips():
    first, second = golden_examples(77), golden_examples(77)
    source = hashlib.sha256(first.image_bytes).hexdigest()
    assert first.image_bytes == second.image_bytes
    for name in ("keypoints", "matches", "verified", "mask"):
        a, b = getattr(first, name), getattr(second, name)
        assert a.source_sha256 == b.source_sha256 == source
        assert a.to_npz() == b.to_npz()
    assert first.clusters.to_json() == second.clusters.to_json()
    assert Keypoints.from_npz(first.keypoints.to_npz()).source_sha256 == source
    assert Matches.from_npz(first.matches.to_npz()).num_keypoints == len(first.keypoints.xy)
    assert Clusters.from_json(first.clusters.to_json()).num_matches == len(first.matches.pairs)
    assert Verified.from_npz(first.verified.to_npz()).cluster_ids == (0,)
    mask = Mask.from_png(first.mask.to_png(), metadata=first.mask.to_json(), source_sha256=source)
    np.testing.assert_array_equal(mask.mask, first.mask.mask)


def test_golden_points_and_transform_match_the_applied_copy_motion():
    golden = golden_examples(77)
    points, pairs = golden.keypoints.xy, golden.matches.pairs
    displacement = points[pairs[:, 1]] - points[pairs[:, 0]]
    np.testing.assert_array_equal(displacement, np.tile([60, 50], (4, 1)))
    matrix = golden.verified.transforms[0].matrix
    source = np.column_stack((points[pairs[:, 0]], np.ones(4)))
    np.testing.assert_array_equal(source @ matrix.T, points[pairs[:, 1]])
    assert golden.verified.transforms[0].flags == ("synthetic_ground_truth",)

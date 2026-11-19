"""Deterministic interchange examples from the real synthetic copy-move generator.

The transform is declared synthetic ground truth, not a detector accuracy result.
"""

import hashlib
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

from photoproof.contracts.copymove import (
    Cluster,
    Clusters,
    Keypoints,
    Mask,
    Matches,
    Region,
    Verified,
    VerifiedTransform,
)
from tests.fixtures.synthetic import copy_move, textured_scene


@dataclass(frozen=True)
class GoldenExamples:
    image_bytes: bytes
    keypoints: Keypoints
    matches: Matches
    clusters: Clusters
    verified: Verified
    mask: Mask


def golden_examples(seed: int = 77) -> GoldenExamples:
    fixture = copy_move(textured_scene(128, 160, seed=seed), translate=(50, 60))
    stream = io.BytesIO()
    Image.fromarray(fixture.image).save(stream, format="PNG")
    original = stream.getvalue()
    source = hashlib.sha256(original).hexdigest()
    sy, sx, ey, ex = (int(v) for v in fixture.meta["source_bbox"])
    ty, tx, by, bx = (int(v) for v in fixture.meta["target_bbox"])
    dy, dx = ty - sy, tx - sx
    points = np.array(
        [[sx + 2, sy + 2], [sx + 6, sy + 2], [sx + 2, sy + 6], [sx + 6, sy + 6]], np.float32
    )
    xy = np.vstack((points, points + np.array([dx, dy], dtype=np.float32))).astype(np.float32)
    descriptors = np.random.default_rng(seed + 1).random((4, 128), dtype=np.float32)
    features = Keypoints(
        source_sha256=source,
        image_size=(160, 128),
        xy=xy,
        scale=np.ones(8, np.float32),
        angle=np.zeros(8, np.float32),
        response=np.ones(8, np.float32),
        desc=np.vstack((descriptors, descriptors)),
    )
    matches = Matches(
        source_sha256=source,
        image_size=features.image_size,
        num_keypoints=8,
        pairs=np.column_stack((np.arange(4), np.arange(4, 8))).astype(np.int32),
        distance=np.zeros(4, np.float32),
    )
    clusters = Clusters(
        source_sha256=source,
        image_size=features.image_size,
        num_matches=4,
        clusters=(
            Cluster(
                id=0,
                pair_indices=(0, 1, 2, 3),
                source_bbox=(sy, sx, ey, ex),
                target_bbox=(ty, tx, by, bx),
                mean_displacement=(dy, dx),
            ),
        ),
    )
    verified = Verified(
        source_sha256=source,
        image_size=features.image_size,
        cluster_ids=(0,),
        transforms=(
            VerifiedTransform(
                cluster_id=0,
                matrix=np.array([[1, 0, dx], [0, 1, dy]], np.float64),
                inliers=4,
                rmse=0,
                scale=1,
                rotation_deg=0,
                score=1,
                flags=("synthetic_ground_truth",),
            ),
        ),
    )
    mask = Mask(
        source_sha256=source,
        image_size=features.image_size,
        mask=fixture.mask,
        regions=(
            Region(
                kind="source", bbox=(sy, sx, ey, ex), area=int(np.count_nonzero(fixture.mask == 1))
            ),
            Region(
                kind="target", bbox=(ty, tx, by, bx), area=int(np.count_nonzero(fixture.mask == 2))
            ),
        ),
    )
    return GoldenExamples(original, features, matches, clusters, verified, mask)

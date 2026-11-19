"""Pure NumPy SIFT descriptor matching with KNN and generalized Lowe ratio filtering.

Implements the second stage of the copy-move detection chain:
keypoints → matches → clusters → verified transforms → masks.

Uses OpenCV FLANN for KNN descriptor matching (brute-force for small sets),
generalized 2NN (g2NN) ratio test, spatial distance constraints, and
symmetric deduplication.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

#: Default random seed for FLANN determinism
_DEFAULT_RANDOM_SEED = 42

#: Default checks parameter for FLANN (number of times the tree is traversed)
_DEFAULT_FLANN_CHECKS = 32

#: Maximum keypoints for brute-force matching (above this, FLANN is used)
_DEFAULT_BF_THRESHOLD = 5000

#: Type alias for OpenCV DMatch objects
DMatchList = list[list[cv2.DMatch]]


@dataclass(frozen=True)
class MatchResult:
    """SIFT descriptor matching result."""

    pairs: np.ndarray  # (M, 2) int32: index pairs (i, j) into keypoint arrays
    distances: np.ndarray  # (M,) float32: match distances
    num_keypoints: int
    num_matches: int
    matcher_used: str  # "flann" or "brute_force"
    config: dict[str, Any]  # Configuration used


def _compute_spatial_distance(kpts: np.ndarray, i: int, j: int) -> float:
    """Compute Euclidean distance between two keypoints."""
    return float(np.sqrt((kpts[i, 0] - kpts[j, 0]) ** 2 + (kpts[i, 1] - kpts[j, 1]) ** 2))


def _match_keypoints(
    keypoints: np.ndarray,
    descriptors: np.ndarray,
    k: int,
    ratio: float,
    min_spatial_distance: float,
    max_pairs: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Perform KNN descriptor matching with g2NN ratio test and spatial constraints.

    Returns (pairs, distances) sorted by distance.
    """
    n = descriptors.shape[0]
    if n < 2:
        return np.zeros((0, 2), dtype=np.int32), np.zeros((0,), dtype=np.float32)

    # Decide matcher: brute force for small sets, FLANN for larger ones
    use_flann = n > _DEFAULT_BF_THRESHOLD

    if use_flann:
        matches = _flann_knn_match(descriptors, k)
    else:
        matches = _brute_force_knn_match(descriptors, k)

    # Apply g2NN ratio test and spatial constraints
    pairs_list: list[tuple[int, int]] = []
    distances_list: list[float] = []

    for mlist in matches:
        if len(mlist) < 2:
            continue

        # Remove self/adjacent structures before the ratio test. Repeated
        # descriptors can include several zero-distance copies: require a
        # positive separating gap, then retain the complete preceding group.
        neighbors = sorted(
            (
                m
                for m in mlist
                if m.queryIdx != m.trainIdx
                and _compute_spatial_distance(keypoints, int(m.queryIdx), int(m.trainIdx))
                >= min_spatial_distance
            ),
            key=lambda m: (m.distance, m.trainIdx),
        )
        for index in range(len(neighbors) - 1):
            left, right = neighbors[index], neighbors[index + 1]
            if right.distance > 0 and left.distance < ratio * right.distance:
                for candidate in neighbors[: index + 1]:
                    pairs_list.append((int(candidate.queryIdx), int(candidate.trainIdx)))
                    distances_list.append(float(candidate.distance))
                break

    if not pairs_list:
        return np.zeros((0, 2), dtype=np.int32), np.zeros((0,), dtype=np.float32)

    pairs = np.array(pairs_list, dtype=np.int32)
    distances = np.array(distances_list, dtype=np.float32)

    # Sort by distance (ascending)
    sort_idx = np.lexsort((pairs[:, 1], pairs[:, 0], distances))
    pairs = pairs[sort_idx]
    distances = distances[sort_idx]

    # Deduplicate symmetric pairs (i,j) and (j,i)
    seen: set[tuple[int, int]] = set()
    deduped_pairs: list[list[int]] = []
    deduped_distances: list[float] = []
    for i in range(len(pairs)):
        a, b = int(pairs[i, 0]), int(pairs[i, 1])
        key = (min(a, b), max(a, b))
        if key not in seen:
            seen.add(key)
            deduped_pairs.append([a, b])
            deduped_distances.append(distances[i])

    if not deduped_pairs:
        return np.zeros((0, 2), dtype=np.int32), np.zeros((0,), dtype=np.float32)

    pairs = np.array(deduped_pairs, dtype=np.int32)
    distances = np.array(deduped_distances, dtype=np.float32)

    # Limit to max_pairs
    if len(pairs) > max_pairs:
        pairs = pairs[:max_pairs]
        distances = distances[:max_pairs]

    return pairs, distances


def _brute_force_knn_match(descriptors: np.ndarray, k: int) -> DMatchList:
    """Brute-force KNN descriptor matching using OpenCV BFMatcher."""
    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
    knn_matches = matcher.knnMatch(descriptors, descriptors, k=min(k + 1, len(descriptors)))
    return cast(DMatchList, knn_matches)


def _flann_knn_match(descriptors: np.ndarray, k: int) -> DMatchList:
    """FLANN-based KNN descriptor matching with fixed seed for determinism."""
    # FLANN parameters for SIFT (L2 norm)
    index_params: dict[str, bool | int | float | str] = dict(
        algorithm=cv2.flann.KDTREE,  # type: ignore[attr-defined]
        trees=4,
    )
    search_params: dict[str, bool | int | float | str] = dict(checks=_DEFAULT_FLANN_CHECKS)
    cv2.setRNGSeed(_DEFAULT_RANDOM_SEED)
    matcher = cv2.FlannBasedMatcher(index_params, search_params)
    knn_matches = matcher.knnMatch(descriptors, descriptors, k=min(k + 1, len(descriptors)))
    return cast(DMatchList, knn_matches)


def analyze_sift_matching(
    keypoints: np.ndarray,
    descriptors: np.ndarray,
    *,
    k: int = 10,
    ratio: float = 0.5,
    min_spatial_distance: float = 10.0,
    max_pairs: int = 50000,
) -> dict[str, Any]:
    """Match SIFT descriptors using KNN and generalized Lowe ratio filtering.

    Returns a dict with:
        - pairs: (M, 2) int32 array of keypoint index pairs
        - distances: (M,) float32 array of match distances
        - num_keypoints: int
        - num_matches: int
        - matcher_used: str ("flann" or "brute_force")
        - config: dict with configuration used

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if (
        isinstance(k, bool)
        or not isinstance(k, int)
        or not 2 <= k <= 50
        or isinstance(max_pairs, bool)
        or not isinstance(max_pairs, int)
        or not 1 <= max_pairs <= 1_000_000
        or not math.isfinite(ratio)
        or not 0 <= ratio <= 1
        or not math.isfinite(min_spatial_distance)
        or not 0 <= min_spatial_distance <= 1000
    ):
        raise InputRejectedError(
            "matching parameters exceed the documented k/ratio/distance/pair bounds"
        )
    if not isinstance(keypoints, np.ndarray) or not isinstance(descriptors, np.ndarray):
        raise InputRejectedError("expected NumPy ndarrays for keypoints and descriptors")
    if keypoints.ndim != 2 or keypoints.shape[1] != 5:
        raise InputRejectedError(f"keypoints must have shape (N, 5), got {keypoints.shape}")
    if descriptors.ndim != 2 or descriptors.shape[1] != 128:
        raise InputRejectedError(f"descriptors must have shape (N, 128), got {descriptors.shape}")
    if keypoints.shape[0] != descriptors.shape[0]:
        raise InputRejectedError(
            f"keypoints ({keypoints.shape[0]}) and descriptors "
            f"({descriptors.shape[0]}) must have same count"
        )
    if (
        keypoints.dtype.kind not in "iuf"
        or descriptors.dtype.kind not in "iuf"
        or not np.isfinite(keypoints).all()
        or not np.isfinite(descriptors).all()
    ):
        raise InputRejectedError("matching needs finite numeric keypoints and descriptors")

    n_keypoints = keypoints.shape[0]
    if n_keypoints < 2:
        return {
            "pairs": np.zeros((0, 2), dtype=np.int32),
            "distances": np.zeros((0,), dtype=np.float32),
            "num_keypoints": int(n_keypoints),
            "num_matches": 0,
            "matcher_used": "brute_force",
            "config": {
                "k": k,
                "ratio": ratio,
                "min_spatial_distance": min_spatial_distance,
                "max_pairs": max_pairs,
                "opencv_version": cv2.__version__,
            },
        }

    pairs, distances = _match_keypoints(
        keypoints, descriptors, k, ratio, min_spatial_distance, max_pairs
    )

    # Apply spatial distance constraint
    if len(pairs) > 0:
        keep_mask = np.ones(len(pairs), dtype=bool)
        for i in range(len(pairs)):
            a, b = int(pairs[i, 0]), int(pairs[i, 1])
            dist = _compute_spatial_distance(keypoints, a, b)
            if dist < min_spatial_distance:
                keep_mask[i] = False
        pairs = pairs[keep_mask]
        distances = distances[keep_mask]

    config: dict[str, Any] = {
        "k": k,
        "ratio": ratio,
        "min_spatial_distance": min_spatial_distance,
        "max_pairs": max_pairs,
        "opencv_version": cv2.__version__,
    }

    return {
        "pairs": pairs,
        "distances": distances,
        "num_keypoints": int(n_keypoints),
        "num_matches": len(pairs),
        "matcher_used": "flann" if n_keypoints > _DEFAULT_BF_THRESHOLD else "brute_force",
        "config": config,
    }


def sift_matching_result_to_dict(result: dict[str, Any]) -> dict[str, Any]:
    """Convert analysis result to plain dict for plugin output."""
    return {
        "pairs": result["pairs"].tolist(),
        "distances": result["distances"].tolist(),
        "num_keypoints": result["num_keypoints"],
        "num_matches": result["num_matches"],
        "matcher_used": result["matcher_used"],
        "config": result["config"],
    }

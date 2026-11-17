"""Copy-move region mask plugin.

Generates pixel-level localization masks from copy-move matches using
bounded ZNCC correlation around matched keypoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError


@dataclass(frozen=True)
class CopyMoveMaskResult:
    """Copy-move localization mask result."""

    mask: np.ndarray  # (H, W) uint8: 0=background, 1=source, 2=target
    source_mask: np.ndarray  # (H, W) uint8: source region mask
    target_mask: np.ndarray  # (H, W) uint8: target region mask
    num_clusters: int
    method: str  # "zncc"
    config: dict[str, Any]  # Configuration used


def _compute_zncc(src_patch: np.ndarray, tgt_patch: np.ndarray) -> float:
    """Compute Zero-mean Normalized Cross-Correlation between two patches.

    The numerator uses centered patches and the denominator is the product of
    the centered (zero-mean) energies, so the score is invariant to additive
    brightness offsets and correctly normalized for contrast changes.  Patches
    with zero or near-zero variance yield 0.0 instead of NaN.
    """
    if src_patch.size == 0 or tgt_patch.size == 0:
        return 0.0
    src_flat = src_patch.astype(np.float64).flatten()
    tgt_flat = tgt_patch.astype(np.float64).flatten()
    if len(src_flat) != len(tgt_flat):
        return 0.0
    src_centered = src_flat - np.mean(src_flat)
    tgt_centered = tgt_flat - np.mean(tgt_flat)
    src_energy = float(np.sum(src_centered**2))
    tgt_energy = float(np.sum(tgt_centered**2))
    if src_energy <= 0.0 or tgt_energy <= 0.0:
        return 0.0
    denom = np.sqrt(src_energy * tgt_energy)
    return float(np.sum(src_centered * tgt_centered) / denom)


def _cluster_pairs(
    keypoints: np.ndarray,
    valid_pairs: np.ndarray,
    min_cluster_size: int,
) -> list[np.ndarray]:
    """Group valid matched pairs into spatially coherent clusters.

    Pairs are linked when their source keypoints lie within one patch radius
    of each other.  Only clusters with at least ``min_cluster_size`` members
    are returned.
    """
    if len(valid_pairs) == 0:
        return []

    n = len(valid_pairs)
    visited = np.zeros(n, dtype=bool)
    clusters: list[np.ndarray] = []

    for i in range(n):
        if visited[i]:
            continue
        # BFS to find all pairs in this cluster
        cluster_idx: list[int] = []
        stack = [i]
        visited[i] = True
        while stack:
            cur = stack.pop()
            cluster_idx.append(cur)
            cur_src = keypoints[valid_pairs[cur, 0], :2]
            for j in range(n):
                if visited[j]:
                    continue
                j_src = keypoints[valid_pairs[j, 0], :2]
                if np.linalg.norm(cur_src - j_src) <= 1.0:
                    visited[j] = True
                    stack.append(j)
        if len(cluster_idx) >= min_cluster_size:
            clusters.append(valid_pairs[cluster_idx])

    return clusters


def _compute_local_correlation(
    keypoints: np.ndarray,
    pairs: np.ndarray,
    image: np.ndarray,
    window_size: int = 11,
) -> np.ndarray:
    """Compute ZNCC around each matched keypoint pair.

    Keypoints are stored as ``[x, y, scale, angle, response]``; NumPy images
    are indexed as ``image[row, col]`` = ``image[y, x]``.
    """
    h, w = image.shape[:2]
    half = window_size // 2
    scores = np.zeros(len(pairs), dtype=np.float32)
    for i in range(len(pairs)):
        a, b = int(pairs[i, 0]), int(pairs[i, 1])
        if a >= keypoints.shape[0] or b >= keypoints.shape[0]:
            scores[i] = 0.0
            continue
        # Keypoints are [x, y]; image indexing is image[y, x].
        sx, sy = int(keypoints[a, 0]), int(keypoints[a, 1])
        tx, ty = int(keypoints[b, 0]), int(keypoints[b, 1])
        src_patch = image[
            max(0, sy - half) : min(h, sy + half + 1),
            max(0, sx - half) : min(w, sx + half + 1),
        ]
        tgt_patch = image[
            max(0, ty - half) : min(h, ty + half + 1),
            max(0, tx - half) : min(w, tx + half + 1),
        ]
        if src_patch.size == 0 or tgt_patch.size == 0:
            scores[i] = 0.0
        else:
            scores[i] = _compute_zncc(src_patch, tgt_patch)
    return scores


def analyze_copy_move(
    keypoints: np.ndarray,
    pairs: np.ndarray,
    image: np.ndarray,
    *,
    window_size: int = 11,
    threshold: float = 0.5,
    min_cluster_size: int = 5,
) -> dict[str, Any]:
    """Analyze copy-move masks from decoded image and matched keypoints.

    Returns a dict with:
        - mask: (H, W) uint8: 0=background, 1=source, 2=target
        - source_mask: (H, W) uint8
        - target_mask: (H, W) uint8
        - num_clusters: int
        - method: str ("zncc")
        - config: dict with configuration used

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(keypoints, np.ndarray) or not isinstance(pairs, np.ndarray):
        raise InputRejectedError("expected NumPy ndarrays for keypoints and pairs")
    if keypoints.ndim != 2 or keypoints.shape[1] != 5:
        raise InputRejectedError(f"keypoints must have shape (N, 5), got {keypoints.shape}")
    if pairs.ndim != 2 or pairs.shape[1] != 2:
        raise InputRejectedError(f"pairs must have shape (M, 2), got {pairs.shape}")
    if not isinstance(image, np.ndarray):
        raise InputRejectedError(f"image must be a NumPy ndarray, got {type(image).__name__}")
    if image.ndim not in (2, 3):
        raise InputRejectedError(f"image must have 2 or 3 dimensions, got {image.ndim}")

    h, w = image.shape[:2]
    n_pairs = pairs.shape[0]

    if n_pairs == 0:
        return {
            "mask": np.zeros((h, w), dtype=np.uint8),
            "source_mask": np.zeros((h, w), dtype=np.uint8),
            "target_mask": np.zeros((h, w), dtype=np.uint8),
            "num_clusters": 0,
            "method": "zncc",
            "config": {
                "window_size": window_size,
                "threshold": threshold,
                "min_cluster_size": min_cluster_size,
            },
        }

    scores = _compute_local_correlation(keypoints, pairs, image, window_size)
    valid_mask = scores >= threshold
    valid_pairs = pairs[valid_mask]

    if len(valid_pairs) == 0:
        return {
            "mask": np.zeros((h, w), dtype=np.uint8),
            "source_mask": np.zeros((h, w), dtype=np.uint8),
            "target_mask": np.zeros((h, w), dtype=np.uint8),
            "num_clusters": 0,
            "method": "zncc",
            "config": {
                "window_size": window_size,
                "threshold": threshold,
                "min_cluster_size": min_cluster_size,
            },
        }

    # Group valid pairs into spatial clusters and enforce min_cluster_size.
    clusters = _cluster_pairs(keypoints, valid_pairs, min_cluster_size)
    num_clusters = len(clusters)

    source_mask = np.zeros((h, w), dtype=np.uint8)
    target_mask = np.zeros((h, w), dtype=np.uint8)

    for cluster in clusters:
        half = window_size // 2
        for i in range(len(cluster)):
            a, b = int(cluster[i, 0]), int(cluster[i, 1])
            sx, sy = int(keypoints[a, 0]), int(keypoints[a, 1])
            tx, ty = int(keypoints[b, 0]), int(keypoints[b, 1])
            source_mask[
                max(0, sy - half) : min(h, sy + half + 1),
                max(0, sx - half) : min(w, sx + half + 1),
            ] = 1
            target_mask[
                max(0, ty - half) : min(h, ty + half + 1),
                max(0, tx - half) : min(w, tx + half + 1),
            ] = 2

    mask = np.zeros((h, w), dtype=np.uint8)
    mask[source_mask > 0] = 1
    mask[target_mask > 0] = 2

    return {
        "mask": mask,
        "source_mask": source_mask,
        "target_mask": target_mask,
        "num_clusters": num_clusters,
        "method": "zncc",
        "config": {
            "window_size": window_size,
            "threshold": threshold,
            "min_cluster_size": min_cluster_size,
        },
    }


def analyze_copy_move_mask(
    keypoints: np.ndarray,
    pairs: np.ndarray,
    image: np.ndarray,
    *,
    window_size: int = 11,
    threshold: float = 0.5,
    min_cluster_size: int = 5,
) -> dict[str, Any]:
    """Analyze copy-move masks from decoded image and matched keypoints.

    Alias for :func:`analyze_copy_move` for API compatibility.
    """
    return analyze_copy_move(
        keypoints,
        pairs,
        image,
        window_size=window_size,
        threshold=threshold,
        min_cluster_size=min_cluster_size,
    )

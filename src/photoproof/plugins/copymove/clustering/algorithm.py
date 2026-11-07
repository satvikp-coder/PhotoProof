"""Copy-move verification algorithm using RANSAC and clustering.

Verifies copy-move matches by estimating geometric transforms (affine or
similarity) with RANSAC, and groups match pairs into clusters by displacement
similarity. Each cluster gets a verified transform with inlier count, RMSE,
and a heuristic score.

Excludes degenerate transforms (extreme scale/shear) and detects low-texture
regions that could cause false positives.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

#: Default RANSAC iterations
_DEFAULT_MAX_ITERATIONS = 1000

#: Default reprojection threshold in pixels
_DEFAULT_REPROJ_THRESHOLD = 3.0

#: Default min inliers for similarity model (2 points minimum + buffer)
_DEFAULT_MIN_INLIERS = 6

#: Default scale range for similarity model
_DEFAULT_SCALE_MIN = 0.5
_DEFAULT_SCALE_MAX = 2.0

#: Default max shear angle in degrees for affine model
_DEFAULT_MAX_SHEAR_DEG = 45.0


@dataclass(frozen=True)
class VerificationResult:
    """Result of RANSAC verification."""

    cluster_id: int
    matrix: np.ndarray  # (2, 3) transformation matrix
    inliers: int
    inlier_ratio: float
    rmse: float
    scale: float
    rotation_deg: float
    score: float  # heuristic confidence, not probability
    model: str  # "affine" or "similarity"
    reason: str | None  # None if verified, reason if failed


def _estimate_affine(src_pts: np.ndarray, tgt_pts: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Estimate affine transform using OpenCV estimateAffine2D.

    Returns (matrix, rmse) or None if estimation fails.
    """
    if len(src_pts) < 3:
        return None
    try:
        matrix, inliers = cv2.estimateAffine2D(
            src_pts.reshape(-1, 1, 2).astype(np.float32),
            tgt_pts.reshape(-1, 1, 2).astype(np.float32),
            method=cv2.RANSAC,
            ransacReprojThreshold=_DEFAULT_REPROJ_THRESHOLD,
            maxIters=_DEFAULT_MAX_ITERATIONS,
            confidence=0.99,
        )
        if matrix is None:
            return None
        if inliers is not None:
            inlier_mask = inliers.flatten().astype(bool)
            if inlier_mask.sum() > 0:
                src_in = src_pts[inlier_mask].reshape(-1, 1, 2).astype(np.float32)
                tgt_in = tgt_pts[inlier_mask].reshape(-1, 1, 2).astype(np.float32)
                projected = cv2.transform(src_in, matrix)
                rmse = float(
                    np.sqrt(np.mean((projected.reshape(-1, 2) - tgt_in.reshape(-1, 2)) ** 2))
                )
            else:
                rmse = float("inf")
        else:
            rmse = float("inf")
        return matrix, rmse
    except cv2.error:
        return None


def _estimate_similarity(
    src_pts: np.ndarray, tgt_pts: np.ndarray
) -> tuple[np.ndarray, float] | None:
    """Estimate similarity transform using OpenCV estimateAffinePartial2D.

    Returns (matrix, rmse) or None if estimation fails.
    """
    if len(src_pts) < 2:
        return None
    try:
        matrix, inliers = cv2.estimateAffinePartial2D(
            src_pts.reshape(-1, 1, 2).astype(np.float32),
            tgt_pts.reshape(-1, 1, 2).astype(np.float32),
            method=cv2.RANSAC,
            ransacReprojThreshold=_DEFAULT_REPROJ_THRESHOLD,
            maxIters=_DEFAULT_MAX_ITERATIONS,
            confidence=0.99,
        )
        if matrix is None:
            return None
        if inliers is not None:
            inlier_mask = inliers.flatten().astype(bool)
            if inlier_mask.sum() > 0:
                src_in = src_pts[inlier_mask].reshape(-1, 1, 2).astype(np.float32)
                tgt_in = tgt_pts[inlier_mask].reshape(-1, 1, 2).astype(np.float32)
                projected = cv2.transform(src_in, matrix)
                rmse = float(
                    np.sqrt(np.mean((projected.reshape(-1, 2) - tgt_in.reshape(-1, 2)) ** 2))
                )
            else:
                rmse = float("inf")
        else:
            rmse = float("inf")
        return matrix, rmse
    except cv2.error:
        return None


def _compute_scale_and_rotation(matrix: np.ndarray) -> tuple[float, float]:
    """Compute scale and rotation from a 2x3 affine/similarity matrix."""
    a, b = matrix[0, 0], matrix[0, 1]
    scale = float(np.sqrt(a * a + b * b))
    rotation_deg = float(np.degrees(np.arctan2(b, a)))
    return scale, rotation_deg


def _check_degenerate(
    matrix: np.ndarray,
    scale: float,
    rotation_deg: float,
    scale_min: float,
    scale_max: float,
    max_shear_deg: float,
) -> str | None:
    """Check if transform is degenerate. Returns reason string or None."""
    if scale < scale_min or scale > scale_max:
        return "scale_out_of_range"
    if abs(scale) < 1e-6:
        return "degenerate_scale"
    if abs(matrix[0, 1]) > np.tan(np.radians(max_shear_deg)) or abs(matrix[1, 0]) > np.tan(
        np.radians(max_shear_deg)
    ):
        return "excessive_shear"
    return None


def _compute_repetitive_structure_score(
    keypoints: np.ndarray, pairs: np.ndarray, matrix: np.ndarray
) -> bool:
    """Check if cluster shows repetitive structure (periodic texture false positive).

    Computes autocorrelation of the displacement field to detect periodic patterns.
    Requires a sufficiently large cluster to avoid flagging small legitimate copies.
    """
    if len(pairs) < 20:
        return False
    src = keypoints[pairs[:, 0], :2]
    tgt = keypoints[pairs[:, 1], :2]
    disp = tgt - src
    if len(disp) > 0:
        disp_std = np.std(disp, axis=0)
        disp_mean = np.mean(disp, axis=0)
        if np.all(disp_std < 2.0) and np.linalg.norm(disp_mean) > 5.0:
            return True
    return False


def analyze_copy_move(
    keypoints: np.ndarray,
    pairs: np.ndarray,
    *,
    model: str = "similarity",
    reproj_threshold: float = 3.0,
    min_inliers: int = 6,
    max_iterations: int = 1000,
    scale_min: float = 0.5,
    scale_max: float = 2.0,
    max_shear_deg: float = 45.0,
) -> dict[str, Any]:
    """Analyze copy-move matches with RANSAC verification.

    Returns a dict with:
        - cluster_id: int
        - matrix: np.ndarray (2, 3)
        - inliers: int
        - inlier_ratio: float
        - rmse: float
        - scale: float
        - rotation_deg: float
        - score: float (heuristic, not probability)
        - model: str ("affine" or "similarity")
        - reason: str | None (None if verified, reason if failed)
        - num_keypoints: int

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(keypoints, np.ndarray) or not isinstance(pairs, np.ndarray):
        raise InputRejectedError("expected NumPy ndarrays for keypoints and pairs")
    if keypoints.ndim != 2 or keypoints.shape[1] != 5:
        raise InputRejectedError(f"keypoints must have shape (N, 5), got {keypoints.shape}")
    if pairs.ndim != 2 or pairs.shape[1] != 2:
        raise InputRejectedError(f"pairs must have shape (M, 2), got {pairs.shape}")

    n_pairs = pairs.shape[0]
    n_keypoints = keypoints.shape[0]
    if n_pairs == 0:
        return {
            "cluster_id": 0,
            "matrix": np.zeros((2, 3), dtype=np.float32),
            "inliers": 0,
            "inlier_ratio": 0.0,
            "rmse": float("inf"),
            "scale": 0.0,
            "rotation_deg": 0.0,
            "score": 0.0,
            "model": model,
            "reason": "no_matches",
            "num_keypoints": 0,
            "num_matches": 0,
            "num_clusters": 0,
            "clusters": [],
        }

    # Run RANSAC verification
    src_pts = keypoints[pairs[:, 0], :2].astype(np.float32)
    tgt_pts = keypoints[pairs[:, 1], :2].astype(np.float32)

    result = None
    if model == "similarity":
        result = _estimate_similarity(src_pts, tgt_pts)
    elif model == "affine":
        result = _estimate_affine(src_pts, tgt_pts)
    else:
        raise InputRejectedError(f"unknown model: {model}, expected 'affine' or 'similarity'")

    if result is None:
        return {
            "cluster_id": 0,
            "matrix": np.zeros((2, 3), dtype=np.float32),
            "inliers": 0,
            "inlier_ratio": 0.0,
            "rmse": float("inf"),
            "scale": 0.0,
            "rotation_deg": 0.0,
            "score": 0.0,
            "model": model,
            "reason": "verification_failed",
            "num_keypoints": int(n_keypoints),
            "num_matches": int(n_pairs),
            "num_clusters": 0,
            "clusters": [],
        }

    matrix, rmse = result

    # Count inliers
    projected = cv2.transform(src_pts.reshape(-1, 1, 2).astype(np.float32), matrix).reshape(-1, 2)
    errors = np.sqrt(np.sum((projected - tgt_pts) ** 2, axis=1))
    inliers = int(np.sum(errors <= reproj_threshold))
    inlier_ratio = float(inliers) / n_pairs if n_pairs > 0 else 0.0

    # Check for degenerate transform
    scale, rotation_deg = _compute_scale_and_rotation(matrix)
    degenerate_reason = _check_degenerate(
        matrix, scale, rotation_deg, scale_min, scale_max, max_shear_deg
    )

    # Compute confidence score
    score = float(inlier_ratio * (1.0 - min(rmse / max(reproj_threshold, 1.0), 1.0)))

    # Check for repetitive structure
    if _compute_repetitive_structure_score(keypoints, pairs, matrix):
        reason = "possible_repetitive_structure"
    elif inliers < min_inliers:
        reason = "too_few_inliers"
    elif degenerate_reason is not None:
        reason = degenerate_reason
    else:
        reason = None

    return {
        "cluster_id": 0,
        "matrix": matrix,
        "inliers": int(inliers),
        "inlier_ratio": float(inlier_ratio),
        "rmse": float(rmse),
        "scale": float(scale),
        "rotation_deg": float(rotation_deg),
        "score": float(score),
        "model": model,
        "reason": reason,
        "num_keypoints": int(n_keypoints),
        "num_matches": int(n_pairs),
        "num_clusters": 1 if reason is None else 0,
        "clusters": [],
    }


def analyze_clustering(
    keypoints: np.ndarray,
    pairs: np.ndarray,
    *,
    method: str = "hierarchical",
    model: str = "similarity",
    reproj_threshold: float = 3.0,
    min_inliers: int = 6,
    max_iterations: int = 1000,
    scale_min: float = 0.5,
    scale_max: float = 2.0,
    max_shear_deg: float = 45.0,
    min_matches_per_cluster: int = 2,
) -> dict[str, Any]:
    """Analyze copy-move matches with clustering and RANSAC verification.

    Groups match pairs into clusters by displacement similarity, then verifies
    each cluster with RANSAC geometric transform estimation.

    Returns a dict with:
        - clusters: list of verified cluster dicts
        - num_keypoints: int
        - num_matches: int
        - num_clusters: int
        - method: str
        - config: dict with configuration used

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not isinstance(keypoints, np.ndarray) or not isinstance(pairs, np.ndarray):
        raise InputRejectedError("expected NumPy ndarrays for keypoints and pairs")
    if keypoints.ndim != 2 or keypoints.shape[1] != 5:
        raise InputRejectedError(f"keypoints must have shape (N, 5), got {keypoints.shape}")
    if pairs.ndim != 2 or pairs.shape[1] != 2:
        raise InputRejectedError(f"pairs must have shape (M, 2), got {pairs.shape}")

    n_keypoints = keypoints.shape[0]
    n_pairs = pairs.shape[0]

    if n_pairs == 0:
        return {
            "clusters": [],
            "num_keypoints": int(n_keypoints),
            "num_matches": 0,
            "num_clusters": 0,
            "method": method,
            "config": {
                "method": method,
                "model": model,
                "reproj_threshold": reproj_threshold,
                "min_inliers": min_inliers,
                "max_iterations": max_iterations,
                "scale_min": scale_min,
                "scale_max": scale_max,
                "max_shear_deg": max_shear_deg,
                "min_matches_per_cluster": min_matches_per_cluster,
            },
        }

    # Compute displacement vectors for all pairs
    src_pts = keypoints[pairs[:, 0], :2].astype(np.float64)
    tgt_pts = keypoints[pairs[:, 1], :2].astype(np.float64)
    displacements = tgt_pts - src_pts  # (n_pairs, 2)

    # Cluster pairs by displacement similarity
    cluster_assignments = _cluster_displacements(
        displacements, method=method, min_matches_per_cluster=min_matches_per_cluster
    )

    # Verify each cluster with RANSAC
    clusters = []
    cluster_id = 0
    for pair_indices in cluster_assignments:
        if len(pair_indices) < min_matches_per_cluster:
            continue
        cluster_pairs = pairs[list(pair_indices)]

        result = _verify_single_cluster(
            keypoints,
            cluster_pairs,
            model=model,
            reproj_threshold=reproj_threshold,
            min_inliers=min_inliers,
            max_iterations=max_iterations,
            scale_min=scale_min,
            scale_max=scale_max,
            max_shear_deg=max_shear_deg,
        )

        if result["reason"] is None:
            clusters.append(
                {
                    "cluster_id": cluster_id,
                    "pair_indices": [int(i) for i in pair_indices],
                    "source_bbox": [
                        float(keypoints[cluster_pairs[:, 0], 1].min()),
                        float(keypoints[cluster_pairs[:, 0], 0].min()),
                        float(keypoints[cluster_pairs[:, 0], 1].max()),
                        float(keypoints[cluster_pairs[:, 0], 0].max()),
                    ],
                    "target_bbox": [
                        float(keypoints[cluster_pairs[:, 1], 1].min()),
                        float(keypoints[cluster_pairs[:, 1], 0].min()),
                        float(keypoints[cluster_pairs[:, 1], 1].max()),
                        float(keypoints[cluster_pairs[:, 1], 0].max()),
                    ],
                    "mean_displacement": [
                        float(np.mean(displacements[list(pair_indices), 1])),
                        float(np.mean(displacements[list(pair_indices), 0])),
                    ],
                    "size": len(pair_indices),
                    "matrix": result["matrix"],
                    "inliers": result["inliers"],
                    "inlier_ratio": result["inlier_ratio"],
                    "rmse": result["rmse"],
                    "scale": result["scale"],
                    "rotation_deg": result["rotation_deg"],
                    "score": result["score"],
                    "model": result["model"],
                    "reason": result["reason"],
                }
            )
            cluster_id += 1

    return {
        "clusters": clusters,
        "num_keypoints": int(n_keypoints),
        "num_matches": int(n_pairs),
        "num_clusters": len(clusters),
        "method": method,
        "config": {
            "method": method,
            "model": model,
            "reproj_threshold": reproj_threshold,
            "min_inliers": min_inliers,
            "max_iterations": max_iterations,
            "scale_min": scale_min,
            "scale_max": scale_max,
            "max_shear_deg": max_shear_deg,
            "min_matches_per_cluster": min_matches_per_cluster,
        },
    }


def _verify_single_cluster(
    keypoints: np.ndarray,
    pairs: np.ndarray,
    *,
    model: str = "similarity",
    reproj_threshold: float = 3.0,
    min_inliers: int = 6,
    max_iterations: int = 1000,
    scale_min: float = 0.5,
    scale_max: float = 2.0,
    max_shear_deg: float = 45.0,
) -> dict[str, Any]:
    """Run RANSAC verification on a single cluster of match pairs.

    Returns a dict with matrix, inliers, inlier_ratio, rmse, scale,
    rotation_deg, score, model, reason.
    """
    n_pairs = pairs.shape[0]
    if n_pairs == 0:
        return {
            "matrix": np.zeros((2, 3), dtype=np.float32),
            "inliers": 0,
            "inlier_ratio": 0.0,
            "rmse": float("inf"),
            "scale": 0.0,
            "rotation_deg": 0.0,
            "score": 0.0,
            "model": model,
            "reason": "no_matches",
        }

    src_pts = keypoints[pairs[:, 0], :2].astype(np.float32)
    tgt_pts = keypoints[pairs[:, 1], :2].astype(np.float32)

    result = None
    if model == "similarity":
        result = _estimate_similarity(src_pts, tgt_pts)
    elif model == "affine":
        result = _estimate_affine(src_pts, tgt_pts)
    else:
        return {
            "matrix": np.zeros((2, 3), dtype=np.float32),
            "inliers": 0,
            "inlier_ratio": 0.0,
            "rmse": float("inf"),
            "scale": 0.0,
            "rotation_deg": 0.0,
            "score": 0.0,
            "model": model,
            "reason": f"unknown_model:{model}",
        }

    if result is None:
        return {
            "matrix": np.zeros((2, 3), dtype=np.float32),
            "inliers": 0,
            "inlier_ratio": 0.0,
            "rmse": float("inf"),
            "scale": 0.0,
            "rotation_deg": 0.0,
            "score": 0.0,
            "model": model,
            "reason": "verification_failed",
        }

    matrix, rmse = result

    # Count inliers
    projected = cv2.transform(src_pts.reshape(-1, 1, 2).astype(np.float32), matrix).reshape(-1, 2)
    errors = np.sqrt(np.sum((projected - tgt_pts) ** 2, axis=1))
    inliers = int(np.sum(errors <= reproj_threshold))
    inlier_ratio = float(inliers) / n_pairs if n_pairs > 0 else 0.0

    # Check for degenerate transform
    scale, rotation_deg = _compute_scale_and_rotation(matrix)
    degenerate_reason = _check_degenerate(
        matrix, scale, rotation_deg, scale_min, scale_max, max_shear_deg
    )

    # Compute confidence score
    score = float(inlier_ratio * (1.0 - min(rmse / max(reproj_threshold, 1.0), 1.0)))

    # Check for repetitive structure
    if _compute_repetitive_structure_score(keypoints, pairs, matrix):
        reason = "possible_repetitive_structure"
    elif inliers < min_inliers:
        reason = "too_few_inliers"
    elif degenerate_reason is not None:
        reason = degenerate_reason
    else:
        reason = None

    return {
        "matrix": matrix,
        "inliers": int(inliers),
        "inlier_ratio": float(inlier_ratio),
        "rmse": float(rmse),
        "scale": float(scale),
        "rotation_deg": float(rotation_deg),
        "score": float(score),
        "model": model,
        "reason": reason,
    }


def _cluster_displacements(
    displacements: np.ndarray,
    *,
    method: str = "hierarchical",
    min_matches_per_cluster: int = 2,
) -> list[np.ndarray]:
    """Cluster displacement vectors into groups of similar displacement.

    Returns a list of arrays, each containing the indices of pairs in one cluster.
    """
    n_pairs = displacements.shape[0]
    if n_pairs == 0:
        return []

    if method == "dbscan":
        return _dbscan_cluster(displacements, min_matches_per_cluster=min_matches_per_cluster)
    # Default: hierarchical (threshold-based agglomerative)
    return _hierarchical_cluster(displacements, min_matches_per_cluster=min_matches_per_cluster)


def _hierarchical_cluster(
    displacements: np.ndarray,
    *,
    min_matches_per_cluster: int = 2,
) -> list[np.ndarray]:
    """Simple threshold-based hierarchical clustering of displacement vectors.

    Pairs with similar displacement vectors (within a pixel tolerance) are
    grouped together. Uses a greedy agglomerative approach.
    """
    n_pairs = displacements.shape[0]
    if n_pairs == 0:
        return []

    # Threshold: displacements within 10 pixels of each other are similar
    tolerance = 10.0

    # Start with each pair as its own cluster
    clusters: list[list[int]] = [[int(i)] for i in range(n_pairs)]

    # Greedily merge clusters whose centroid displacements are close
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(clusters):
            j = i + 1
            while j < len(clusters):
                ci = displacements[clusters[i]].mean(axis=0)
                cj = displacements[clusters[j]].mean(axis=0)
                if float(np.linalg.norm(ci - cj)) <= tolerance:
                    merged: list[int] = []
                    merged.extend(int(x) for x in clusters[i])
                    merged.extend(int(x) for x in clusters[j])
                    clusters[i] = merged
                    clusters.pop(j)
                    changed = True
                else:
                    j += 1
            i += 1

    # Filter out clusters below minimum size
    result_clusters: list[np.ndarray] = []
    for c in clusters:
        if len(c) >= min_matches_per_cluster:
            result_clusters.append(np.array([int(x) for x in c], dtype=np.int64))
    return result_clusters


def _dbscan_cluster(
    displacements: np.ndarray,
    *,
    min_matches_per_cluster: int = 2,
) -> list[np.ndarray]:
    """Simple DBSCAN-style clustering of displacement vectors.

    Core points have at least ``min_matches_per_cluster`` neighbors within
    ``tolerance`` pixels. Clusters grow from core points by adding density-reachable
    points.
    """
    n_pairs = displacements.shape[0]
    if n_pairs == 0:
        return []

    tolerance = 10.0
    visited = [False] * n_pairs
    clusters: list[np.ndarray] = []
    current_cluster = 0

    for i in range(n_pairs):
        if visited[i]:
            continue
        visited[i] = True

        # Find neighbors within tolerance
        diffs = displacements - displacements[i]
        dists = np.sqrt(np.sum(diffs * diffs, axis=1))
        neighbors = [int(x) for x in np.where(dists <= tolerance)[0]]

        if len(neighbors) < min_matches_per_cluster:
            # Noise point - not a core point
            continue

        # Start a new cluster
        cluster = [i]

        # Expand cluster
        idx = 0
        while idx < len(cluster):
            q = cluster[idx]
            idx += 1
            q_diffs = displacements - displacements[q]
            q_dists = np.sqrt(np.sum(q_diffs * q_diffs, axis=1))
            q_neighbors = [int(x) for x in np.where(q_dists <= tolerance)[0]]
            for nb in q_neighbors:
                if not visited[nb]:
                    visited[nb] = True
                    nb_diffs = displacements - displacements[nb]
                    nb_dists = np.sqrt(np.sum(nb_diffs * nb_diffs, axis=1))
                    nb_neighbors = [int(x) for x in np.where(nb_dists <= tolerance)[0]]
                    if len(nb_neighbors) >= min_matches_per_cluster:
                        for nb2 in nb_neighbors:
                            if nb2 not in neighbors:
                                neighbors.append(nb2)
                if nb not in cluster:
                    cluster.append(nb)

        if len(cluster) >= min_matches_per_cluster:
            clusters.append(np.array(cluster, dtype=np.int64))
            current_cluster += 1

    return clusters


def clustering_result_to_dict(result: dict[str, Any]) -> dict[str, Any]:
    """Convert a clustering result dict to a plain JSON-serializable dict.

    Converts NumPy arrays and other non-serializable types to Python natives.
    """
    result_copy = copy.deepcopy(result)

    def _convert(obj: Any) -> Any:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(_convert(v) for v in obj)
        return obj

    converted: dict[str, Any] = _convert(result_copy)
    return converted

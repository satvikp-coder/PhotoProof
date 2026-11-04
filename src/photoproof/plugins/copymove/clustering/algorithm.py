"""Copy-move clustering algorithm.

Groups matched keypoint pairs into coherent clusters using hierarchical clustering
(Ward linkage) or DBSCAN on displacement vectors. Each cluster represents a
hypothesis for one copied region.

Extracted from issue #22 - clustered matched keypoints by displacement vectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError

#: Default random seed for reproducibility
_DEFAULT_RANDOM_SEED = 42


@dataclass(frozen=True)
class ClusterResult:
    """Clustering result."""

    clusters: list[dict[str, Any]]  # List of cluster dicts
    num_keypoints: int
    num_matches: int
    num_clusters: int
    method: str  # "hierarchical" or "dbscan"
    config: dict[str, Any]  # Configuration used


def _normalize_pair_direction(kpts: np.ndarray, pairs: np.ndarray) -> np.ndarray:
    """Normalize pair direction so that (A->B) and (B->A) collapse.

    For each pair (i, j), if keypoint i has a larger x-coordinate than j,
    swap them so the smaller-x keypoint is always first. This ensures
    that (i,j) and (j,i) map to the same displacement vector.
    """
    normalized = pairs.copy()
    for i in range(len(pairs)):
        a, b = int(pairs[i, 0]), int(pairs[i, 1])
        if kpts[a, 0] > kpts[b, 0]:
            pairs[i, 0] = b
            pairs[i, 1] = a
    return normalized


def _compute_displacement_vectors(kpts: np.ndarray, pairs: np.ndarray) -> np.ndarray:
    """Compute displacement vectors for each matched pair."""
    src = kpts[pairs[:, 0], :2]  # Source keypoints (x, y)
    tgt = kpts[pairs[:, 1], :2]  # Target keypoints (x, y)
    displacements: np.ndarray = tgt - src
    return displacements


def _build_clusters(
    pairs: np.ndarray,
    displacements: np.ndarray,
    labels: np.ndarray,
    min_matches_per_cluster: int,
    kpts: np.ndarray,
) -> list[dict[str, Any]]:
    """Build cluster dicts from cluster labels.

    Each cluster dict contains:
        - cluster_id: int
        - pair_indices: list of pair indices into the pairs array
        - source_bbox: [y0, x0, y1, x1] bounding box of source keypoints
        - target_bbox: [y0, x0, y1, x1] bounding box of target keypoints
        - mean_displacement: [dy, dx] mean displacement vector
        - size: number of matched pairs in the cluster
    """
    unique_labels = np.unique(labels)
    clusters: list[dict[str, Any]] = []

    cluster_id = 0
    for label in unique_labels:
        if label == -1:
            # Noise cluster - skip
            continue

        mask = labels == label
        cluster_pair_indices = np.where(mask)[0].tolist()
        cluster_size = len(cluster_pair_indices)

        if cluster_size < min_matches_per_cluster:
            continue

        # Get keypoint indices for source and target
        src_indices = pairs[mask][:, 0]
        tgt_indices = pairs[mask][:, 1]

        # Get actual keypoint coordinates
        src_coords = kpts[src_indices][:, :2]  # (N, 2)
        tgt_coords = kpts[tgt_indices][:, :2]  # (N, 2)

        # Compute bounding boxes
        src_bbox = [
            float(src_coords[:, 1].min()),  # y0
            float(src_coords[:, 0].min()),  # x0
            float(src_coords[:, 1].max()),  # y1
            float(src_coords[:, 0].max()),  # x1
        ]
        tgt_bbox = [
            float(tgt_coords[:, 1].min()),  # y0
            float(tgt_coords[:, 0].min()),  # x0
            float(tgt_coords[:, 1].max()),  # y1
            float(tgt_coords[:, 0].max()),  # x1
        ]

        # Mean displacement
        cluster_displacements = displacements[mask]
        mean_displacement = [
            float(cluster_displacements[:, 1].mean()),  # dy
            float(cluster_displacements[:, 0].mean()),  # dx
        ]

        cluster_dict: dict[str, Any] = {
            "cluster_id": int(cluster_id),
            "pair_indices": [int(p) for p in cluster_pair_indices],
            "source_bbox": src_bbox,
            "target_bbox": tgt_bbox,
            "mean_displacement": mean_displacement,
            "size": int(cluster_size),
        }
        clusters.append(cluster_dict)
        cluster_id += 1

    return clusters


def _hierarchical_clustering(
    pairs: np.ndarray,
    displacements: np.ndarray,
    cut_threshold: float,
    min_matches_per_cluster: int,
    kpts: np.ndarray,
) -> list[dict[str, Any]]:
    """Cluster matches using hierarchical clustering (Ward linkage).

    Clusters based on displacement vectors, so pairs with similar
    displacement vectors are grouped together.
    """
    from scipy.cluster.hierarchy import fcluster, linkage  # type: ignore[import-untyped]  # noqa: PLC0415

    n = len(pairs)
    if n < 2:
        return []

    # Compute linkage matrix using Ward linkage
    Z = linkage(displacements, method="ward", metric="euclidean")

    # Extract clusters at the specified threshold
    labels = fcluster(Z, t=cut_threshold, criterion="distance")

    return _build_clusters(pairs, displacements, labels, min_matches_per_cluster, kpts)


def _dbscan_clustering(
    pairs: np.ndarray,
    displacements: np.ndarray,
    eps: float,
    min_matches_per_cluster: int,
    kpts: np.ndarray,
) -> list[dict[str, Any]]:
    """Cluster matches using DBSCAN on displacement vectors."""
    from sklearn.cluster import DBSCAN  # type: ignore[import-untyped]  # noqa: PLC0415

    n = len(pairs)
    if n < 2:
        return []

    dbscan = DBSCAN(eps=eps, min_samples=min_matches_per_cluster, metric="euclidean")
    labels = dbscan.fit_predict(displacements)

    return _build_clusters(pairs, displacements, labels, min_matches_per_cluster, kpts)


def analyze_clustering(
    keypoints: np.ndarray,
    pairs: np.ndarray,
    *,
    method: str = "hierarchical",
    cut_threshold: float = 0.5,
    eps: float = 0.5,
    min_matches_per_cluster: int = 4,
) -> dict[str, Any]:
    """Analyze copy-move matches and cluster them into coherent groups.

    Returns a dict with:
        - clusters: list of cluster dicts with cluster_id, pair_indices,
                    source_bbox, target_bbox, mean_displacement, size
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
                "cut_threshold": cut_threshold,
                "eps": eps,
                "min_matches_per_cluster": min_matches_per_cluster,
            },
        }

    # Normalize pair direction so (A->B) and (B->A) collapse
    normalized_pairs = _normalize_pair_direction(keypoints, pairs.copy())

    # Compute displacement vectors
    displacements: np.ndarray = _compute_displacement_vectors(keypoints, normalized_pairs)

    # Cluster based on method
    if method == "hierarchical":
        clusters = _hierarchical_clustering(
            normalized_pairs, displacements, cut_threshold, min_matches_per_cluster, keypoints
        )
    elif method == "dbscan":
        clusters = _dbscan_clustering(
            normalized_pairs, displacements, eps, min_matches_per_cluster, keypoints
        )
    else:
        raise InputRejectedError(
            f"unknown clustering method: {method}, expected 'hierarchical' or 'dbscan'"
        )

    return {
        "clusters": clusters,
        "num_keypoints": int(n_keypoints),
        "num_matches": int(n_pairs),
        "num_clusters": len(clusters),
        "method": method,
        "config": {
            "method": method,
            "cut_threshold": cut_threshold,
            "eps": eps,
            "min_matches_per_cluster": min_matches_per_cluster,
        },
    }


def clustering_result_to_dict(result: dict[str, Any]) -> dict[str, Any]:
    """Convert analysis result to plain dict for plugin output."""
    return {
        "clusters": result["clusters"],
        "num_keypoints": result["num_keypoints"],
        "num_matches": result["num_matches"],
        "num_clusters": result["num_clusters"],
        "method": result["method"],
        "config": result["config"],
    }

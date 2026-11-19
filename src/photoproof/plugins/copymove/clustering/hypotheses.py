"""Bounded Ward and displacement-density hypotheses; no geometric verification."""

from collections import deque
from typing import Literal

import numpy as np

from photoproof.contracts.copymove import Cluster, Clusters, Keypoints, Matches
from photoproof.contracts.exceptions import InputRejectedError


def _ward(points: np.ndarray, *, threshold: float, minimum: int) -> list[np.ndarray]:
    """Exact Ward linkage using the squared-distance Lance-Williams update.

    The caller enforces the small input limit before this quadratic allocation.
    Stable row-order tie breaking makes fixed-input output deterministic.
    """
    n = len(points)
    if n < minimum:
        return []
    distances = points @ points.T
    distances *= -2
    norm = np.sum(points * points, axis=1)
    distances += norm[:, None]
    distances += norm[None, :]
    np.maximum(distances, 0, out=distances)
    np.fill_diagonal(distances, np.inf)
    active = np.ones(n, dtype=bool)
    sizes = np.ones(n, dtype=np.int64)
    members = [[i] for i in range(n)]
    while np.count_nonzero(active) > 1:
        first, second = np.unravel_index(int(np.argmin(distances)), distances.shape)
        separation = float(distances[first, second])
        if separation > threshold * threshold:
            break
        remaining = np.flatnonzero(active)
        remaining = remaining[(remaining != first) & (remaining != second)]
        weights = sizes[remaining].astype(np.float64)
        left, right = int(sizes[first]), int(sizes[second])
        updated = (
            (weights + left) * distances[remaining, first]
            + (weights + right) * distances[remaining, second]
            - weights * separation
        ) / (weights + left + right)
        updated = np.maximum(updated, 0)
        distances[remaining, first] = updated
        distances[first, remaining] = updated
        distances[second, :] = np.inf
        distances[:, second] = np.inf
        active[second] = False
        sizes[first] += sizes[second]
        members[first].extend(members[second])
        members[second] = []
    return [
        np.asarray(sorted(members[i]), dtype=np.int64)
        for i in np.flatnonzero(active)
        if len(members[i]) >= minimum
    ]


def _density(
    displacements: np.ndarray, *, eps: float, minimum: int, max_work: int
) -> tuple[list[np.ndarray], int]:
    """Weighted DBSCAN with spatial bins, duplicate compression and a work ceiling.

    Query one neighborhood at a time; never allocate a pairwise distance matrix.
    Exceeding the work budget fails explicitly rather than truncating evidence.
    """
    if len(displacements) < minimum:
        return [], 0
    points, inverse, counts = np.unique(
        displacements, axis=0, return_inverse=True, return_counts=True
    )
    cells = np.floor(points / eps).astype(np.int64)
    bins: dict[tuple[int, int], list[int]] = {}
    for i, cell in enumerate(cells):
        bins.setdefault((int(cell[0]), int(cell[1])), []).append(i)
    work = 0

    def neighbors(index: int) -> np.ndarray:
        nonlocal work
        x, y = int(cells[index, 0]), int(cells[index, 1])
        candidates = [
            i for dx in (-1, 0, 1) for dy in (-1, 0, 1) for i in bins.get((x + dx, y + dy), ())
        ]
        work += len(candidates)
        if work > max_work:
            raise InputRejectedError(
                "clustering density work limit exceeded; reduce matching max_pairs or eps"
            )
        indices = np.asarray(candidates, dtype=np.int64)
        delta = points[indices] - points[index]
        return np.sort(indices[np.sum(delta * delta, axis=1) <= eps * eps])

    labels = np.full(len(points), -1, dtype=np.int64)
    visited = np.zeros(len(points), dtype=bool)
    queued = np.zeros(len(points), dtype=bool)
    label = 0
    for seed in range(len(points)):
        if visited[seed]:
            continue
        visited[seed] = True
        adjacent = neighbors(seed)
        if int(np.sum(counts[adjacent])) < minimum:
            continue
        labels[seed] = label
        queue: deque[int] = deque()
        for i in adjacent:
            if not queued[i]:
                queue.append(int(i))
                queued[i] = True
        while queue:
            index = queue.popleft()
            if not visited[index]:
                visited[index] = True
                adjacent = neighbors(index)
                if int(np.sum(counts[adjacent])) >= minimum:
                    for i in adjacent:
                        if not queued[i]:
                            queue.append(int(i))
                            queued[i] = True
            if labels[index] < 0:
                labels[index] = label
        label += 1
        if label > 1000:
            raise InputRejectedError("clustering exceeds the 1000-hypothesis metadata limit")
    return [np.flatnonzero(labels[inverse] == i) for i in range(label)], work


def _box(points: np.ndarray, *, width: int, height: int) -> tuple[int, int, int, int]:
    return (
        max(0, int(np.floor(points[:, 1].min()))),
        max(0, int(np.floor(points[:, 0].min()))),
        min(height, int(np.ceil(points[:, 1].max())) + 1),
        min(width, int(np.ceil(points[:, 0].max())) + 1),
    )


def _coherence_transform(
    source: np.ndarray, target: np.ndarray, *, scale: np.ndarray, rmse: float
) -> np.ndarray | None:
    """Reject plainly inconsistent groups without claiming robust verification.

    A least-squares affine fit uses all group members. It can reject groups with
    outliers and cannot replace RANSAC or establish actual verified inlier identities.
    """
    design = np.column_stack((source / scale, np.ones(len(source))))
    try:
        matrix, _, rank, _ = np.linalg.lstsq(design, target / scale, rcond=None)
    except np.linalg.LinAlgError as error:
        raise InputRejectedError(
            "clustering coherence fit failed; no hypotheses produced"
        ) from error
    if rank < 3:
        return None
    if not np.isfinite(matrix).all():
        raise InputRejectedError("clustering coherence fit is nonfinite; no hypotheses produced")
    residual = (design @ matrix - target / scale) * scale
    if np.sqrt(np.mean(np.sum(residual * residual, axis=1))) > rmse:
        return None
    return np.asarray(matrix, dtype=np.float64)


def _boxes(
    source: np.ndarray, target: np.ndarray, matrix: np.ndarray, *, scale: np.ndarray
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Project the source point envelope so rotation does not lose its corners.

    These are bounded hypothesis boxes, not dense pixel-region localization.
    Observed target points remain included despite residual fit error.
    """
    width, height = int(scale[0]), int(scale[1])
    source_box = _box(source, width=width, height=height)
    y0, x0, y1, x1 = source_box
    corners = np.array([[x0, y0], [x1 - 1, y0], [x0, y1 - 1], [x1 - 1, y1 - 1]])
    projected = (np.column_stack((corners / scale, np.ones(4))) @ matrix) * scale
    if not np.isfinite(projected).all():
        raise InputRejectedError("clustering projected box is nonfinite; no hypotheses produced")
    return source_box, _box(np.vstack((target, projected)), width=width, height=height)


def _near(a: tuple[int, int, int, int], b: tuple[int, int, int, int], *, gap: float) -> bool:
    dy = max(0, a[0] - b[2], b[0] - a[2])
    dx = max(0, a[1] - b[3], b[1] - a[3])
    return dx * dx + dy * dy <= gap * gap


def _merge_neighbors(
    groups: list[tuple[np.ndarray, np.ndarray]],
    source: np.ndarray,
    target: np.ndarray,
    *,
    scale: np.ndarray,
    rmse: float,
    gap: float,
    max_work: int,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], int]:
    """Join adjacent coherent fragments using a fixed original-neighborhood graph.

    A single deterministic edge pass avoids repeated quadratic rescans. Both source
    and target boxes must be neighbors, and every union must pass the all-row fit.
    """
    if len(groups) > 1000:
        raise InputRejectedError("clustering exceeds the 1000-hypothesis metadata limit")
    width, height = int(scale[0]), int(scale[1])
    bounds = [
        (
            _box(source[ids], width=width, height=height),
            _box(target[ids], width=width, height=height),
        )
        for ids, _ in groups
    ]
    parents = list(range(len(groups)))
    current = dict(enumerate(groups))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    work = 0
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            if not _near(bounds[i][0], bounds[j][0], gap=gap) or not _near(
                bounds[i][1], bounds[j][1], gap=gap
            ):
                continue
            left, right = sorted((root(i), root(j)))
            if left == right:
                continue
            first, second = current[left], current[right]
            work += len(first[0]) + len(second[0])
            if work > max_work:
                raise InputRejectedError(
                    "clustering merge work limit exceeded; reduce matching max_pairs"
                )
            indices = np.sort(np.concatenate((first[0], second[0])))
            matrix = _coherence_transform(source[indices], target[indices], scale=scale, rmse=rmse)
            if matrix is not None:
                parents[right] = left
                current[left] = (indices, matrix)
                del current[right]
    return list(current.values()), work


def cluster_matches(
    features: Keypoints,
    matches: Matches,
    *,
    method: Literal["hierarchical", "dbscan"] = "hierarchical",
    cut_threshold: float = 0.5,
    eps: float = 0.5,
    coherence_rmse_px: float = 3.0,
    merge_gap_px: float = 16.0,
    min_matches_per_cluster: int = 4,
    ward_limit: int = 512,
    max_input_pairs: int = 50_000,
    max_neighbor_work: int = 5_000_000,
) -> tuple[Clusters, dict[str, object]]:
    """Group actual candidate pairs in one verified source/frame.

    Ward uses normalized source, target and displacement [x/W,y/H] features.
    The displacement term penalizes incoherent motion within spatial groups.
    DBSCAN uses displacement
    [dx,dy] in working pixels and is primarily a translation hypothesis method.
    The lexicographically smaller [x,y] endpoint defines the source role; this
    convention does not establish which region was historically copied.
    """
    if (
        matches.source_sha256 != features.source_sha256
        or matches.image_size != features.image_size
        or matches.num_keypoints != len(features.xy)
    ):
        raise InputRejectedError("clustering keypoints/matches disagree on source, frame or count")
    for name, value, lower, upper in (
        ("minimum", min_matches_per_cluster, 1, 100),
        ("ward_limit", ward_limit, 2, 1024),
        ("max_input_pairs", max_input_pairs, 1, 50_000),
        ("max_neighbor_work", max_neighbor_work, 1, 10_000_000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise InputRejectedError(f"clustering {name} must be an integer in [{lower},{upper}]")
    if (
        not isinstance(method, str)
        or method not in {"hierarchical", "dbscan"}
        or isinstance(cut_threshold, bool)
        or not isinstance(cut_threshold, (int, float, np.integer, np.floating))
        or not 0 <= cut_threshold <= 1
        or isinstance(eps, bool)
        or not isinstance(eps, (int, float, np.integer, np.floating))
        or not 1e-6 <= eps <= 1000
        or isinstance(coherence_rmse_px, bool)
        or not isinstance(coherence_rmse_px, (int, float, np.integer, np.floating))
        or not 1e-6 <= coherence_rmse_px <= 1000
        or isinstance(merge_gap_px, bool)
        or not isinstance(merge_gap_px, (int, float, np.integer, np.floating))
        or not 0 <= merge_gap_px <= 256
    ):
        raise InputRejectedError("clustering needs a known method and finite bounded thresholds")
    if len(matches.pairs) > max_input_pairs:
        raise InputRejectedError("clustering pair limit exceeded; reduce matching max_pairs")
    pairs = np.array(matches.pairs, copy=True)
    if np.any(pairs[:, 0] == pairs[:, 1]):
        raise InputRejectedError("clustering cannot accept self matches as copy hypotheses")
    source, target = features.xy[pairs[:, 0]], features.xy[pairs[:, 1]]
    reverse = (source[:, 0] > target[:, 0]) | (
        (source[:, 0] == target[:, 0]) & (source[:, 1] > target[:, 1])
    )
    pairs[reverse] = pairs[reverse, ::-1]
    _, retained = np.unique(pairs, axis=0, return_index=True)
    retained = np.sort(retained)
    pairs = pairs[retained]
    source = features.xy[pairs[:, 0]].astype(np.float64)
    target = features.xy[pairs[:, 1]].astype(np.float64)
    displacement = target - source
    effective = method
    fallback = None
    if method == "hierarchical" and len(pairs) > ward_limit:
        effective = "dbscan"
        fallback = "ward_input_limit"
    work = 0
    scale = np.asarray(features.image_size, dtype=np.float64)
    if effective == "hierarchical":
        groups = _ward(
            np.column_stack((source / scale, target / scale, displacement / scale)),
            threshold=cut_threshold,
            minimum=min_matches_per_cluster,
        )
    else:
        groups, work = _density(
            displacement, eps=eps, minimum=min_matches_per_cluster, max_work=max_neighbor_work
        )
    before_filter = len(groups)
    initial_fit_work = sum(len(indices) for indices in groups)
    if work + initial_fit_work > max_neighbor_work:
        raise InputRejectedError(
            "clustering coherence work limit exceeded; reduce matching max_pairs"
        )
    selected = []
    for indices in groups:
        matrix = _coherence_transform(
            source[indices], target[indices], scale=scale, rmse=coherence_rmse_px
        )
        if matrix is not None:
            selected.append((indices, matrix))
    before_merge = len(selected)
    selected, merge_work = _merge_neighbors(
        selected,
        source,
        target,
        scale=scale,
        rmse=coherence_rmse_px,
        gap=merge_gap_px,
        max_work=max_neighbor_work - work - initial_fit_work,
    )
    selected.sort(key=lambda item: int(retained[item[0]].min()))
    clusters = []
    for i, (indices, matrix) in enumerate(selected):
        source_box, target_box = _boxes(source[indices], target[indices], matrix, scale=scale)
        clusters.append(
            Cluster(
                id=i,
                pair_indices=tuple(int(v) for v in retained[indices]),
                source_bbox=source_box,
                target_bbox=target_box,
                mean_displacement=(
                    float(displacement[indices, 1].mean()),
                    float(displacement[indices, 0].mean()),
                ),
            )
        )
    result = Clusters(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        num_matches=len(matches.pairs),
        clusters=tuple(clusters),
    )
    return result, {
        "method_requested": method,
        "method_used": effective,
        "fallback_reason": fallback,
        "num_matches": len(matches.pairs),
        "num_unique_pairs": len(pairs),
        "num_clusters": len(clusters),
        "num_rejected_incoherent": before_filter - before_merge,
        "num_merged_fragments": before_merge - len(selected),
        "num_clustered_pairs": sum(len(c.pair_indices) for c in clusters),
        "density_neighbor_work": work,
        "coherence_fit_work": initial_fit_work,
        "merge_fit_work": merge_work,
        "pair_direction": "lexicographic_working_xy",
        "ward_features": "normalized_source_target_displacement",
        "coherence_filter": "least_squares_affine_rmse",
        "bbox_policy": "source_point_envelope_affine_target",
        "calibration": "uncalibrated",
    }

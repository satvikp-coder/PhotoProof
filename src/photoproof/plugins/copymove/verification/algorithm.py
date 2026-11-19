"""Seeded bounded geometric verification with explicit original-row inlier identities."""

import math
from dataclasses import dataclass

import cv2
import numpy as np

from photoproof.contracts.copymove import (
    Cluster,
    Clusters,
    Keypoints,
    Matches,
    Verified,
    VerifiedTransform,
)
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.verification.periodicity import inspect_repetition
from photoproof.plugins.copymove.verification.schema import Config


@dataclass(frozen=True)
class VerificationOutcome:
    verified: Verified
    inliers: Clusters
    summary: dict[str, object]
    details: tuple[dict[str, object], ...]


def _points(features: Keypoints, pairs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source = features.xy[pairs[:, 0]].astype(np.float64)
    target = features.xy[pairs[:, 1]].astype(np.float64)
    reverse = (source[:, 0] > target[:, 0]) | (
        (source[:, 0] == target[:, 0]) & (source[:, 1] > target[:, 1])
    )
    return np.where(reverse[:, None], target, source), np.where(reverse[:, None], source, target)


def _rank(points: np.ndarray, scale: np.ndarray) -> int:
    try:
        return int(np.linalg.matrix_rank(np.column_stack((points / scale, np.ones(len(points))))))
    except np.linalg.LinAlgError as error:
        raise InputRejectedError(
            "verification geometry rank failed; no verified output produced"
        ) from error


def _box(points: np.ndarray, size: tuple[int, int]) -> tuple[int, int, int, int]:
    return (
        max(0, int(np.floor(points[:, 1].min()))),
        max(0, int(np.floor(points[:, 0].min()))),
        min(size[1], int(np.ceil(points[:, 1].max())) + 1),
        min(size[0], int(np.ceil(points[:, 0].max())) + 1),
    )


def _boxes(
    source: np.ndarray, target: np.ndarray, matrix: np.ndarray, size: tuple[int, int]
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    source_box = _box(source, size)
    y0, x0, y1, x1 = source_box
    corners = np.array([[x0, y0], [x1 - 1, y0], [x0, y1 - 1], [x1 - 1, y1 - 1]], np.float64)
    projected = corners @ matrix[:, :2].T + matrix[:, 2]
    if not np.isfinite(projected).all():
        raise InputRejectedError("verification projected envelope is nonfinite")
    return source_box, _box(np.vstack((target, projected)), size)


def _geometry(matrix: np.ndarray, config: Config) -> tuple[float, float, float, bool] | str:
    linear = matrix[:, :2]
    determinant = float(np.linalg.det(linear))
    if not math.isfinite(determinant) or abs(determinant) <= 1e-8:
        return "near_zero_determinant"
    try:
        singular = np.asarray(np.linalg.svd(linear, compute_uv=False), dtype=np.float64)
    except np.linalg.LinAlgError as error:
        raise InputRejectedError("verification transform decomposition failed") from error
    if (
        not np.isfinite(singular).all()
        or float(singular.min()) < config.scale_min - 1e-6
        or float(singular.max()) > config.scale_max + 1e-6
    ):
        return "scale_out_of_range"
    columns = float(np.linalg.norm(linear[:, 0]) * np.linalg.norm(linear[:, 1]))
    cosine = abs(float(np.dot(linear[:, 0], linear[:, 1]))) / columns
    shear = math.degrees(math.asin(min(1.0, cosine)))
    if shear > config.max_shear_deg + 1e-6:
        return "excessive_shear"
    return (
        math.sqrt(abs(determinant)),
        math.degrees(math.atan2(float(linear[1, 0]), float(linear[0, 0]))),
        shear,
        determinant < 0,
    )


def verify_clusters(
    features: Keypoints,
    matches: Matches,
    clusters: Clusters,
    *,
    image: np.ndarray,
    seed: int,
    config: Config | None = None,
) -> VerificationOutcome:
    """Verify geometry, not forgery; preserve actual parent match-row inlier sets.

    ``score = distinct_inliers/original_rows * exp(-rmse/reproj_threshold)`` is an uncalibrated
    geometric heuristic. Repetitive or insufficient texture caps it conservatively.
    The RANSAC stop-confidence parameter controls sampling, not image authenticity.
    """
    config = Config() if config is None else config
    if (
        not isinstance(config, Config)
        or isinstance(seed, bool)
        or not isinstance(seed, int)
        or seed < 0
    ):
        raise InputRejectedError(
            "verification needs validated config and a nonnegative integer seed"
        )
    if (
        features.source_sha256 != matches.source_sha256
        or features.source_sha256 != clusters.source_sha256
        or features.image_size != matches.image_size
        or features.image_size != clusters.image_size
        or matches.num_keypoints != len(features.xy)
        or clusters.num_matches != len(matches.pairs)
    ):
        raise InputRejectedError("verification inputs disagree on source, frame or parent counts")
    if (
        not isinstance(image, np.ndarray)
        or image.ndim not in (2, 3)
        or image.shape[:2] != (features.image_size[1], features.image_size[0])
        or image.dtype.kind not in "uif"
        or (image.ndim == 3 and image.shape[2] not in (1, 3, 4))
    ):
        raise InputRejectedError(
            "verification image must be numeric pixels in the same working frame"
        )
    if len(matches.pairs) > 50_000:
        raise InputRejectedError(
            "verification input pair limit exceeded; reduce matching max_pairs"
        )
    ransac_work = sum(len(c.pair_indices) for c in clusters.clusters) * config.max_iterations
    if ransac_work > config.max_ransac_work:
        raise InputRejectedError(
            "verification RANSAC work limit exceeded; reduce pairs or iterations"
        )
    scale = np.asarray(features.image_size, dtype=np.float64)
    transforms = []
    inlier_clusters = []
    details: list[dict[str, object]] = []
    rejection_counts: dict[str, int] = {}
    texture_work = 0
    flagged = 0
    cv2.setNumThreads(1)
    for cluster in sorted(clusters.clusters, key=lambda c: c.id):
        indices = np.sort(np.asarray(cluster.pair_indices, dtype=np.int64))
        input_count = len(indices)
        record: dict[str, object] = {"cluster_id": cluster.id, "input_pairs": len(indices)}

        def reject(
            reason: str, detail: str | None = None, *, entry: dict[str, object] = record
        ) -> None:
            entry.update({"status": "rejected", "reason": reason, "detail": detail, "score": None})
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            details.append(entry)

        if len(indices) < config.min_inliers:
            reject("too_few_inliers")
            continue
        pairs = matches.pairs[indices]
        if np.any(pairs[:, 0] == pairs[:, 1]):
            raise InputRejectedError("verification cannot accept self-match indices")
        source, target = _points(features, pairs)
        # Multiple SIFT orientations can share one physical point pair. They are
        # not independent geometric support for the minimum-inlier requirement.
        _, distinct = np.unique(np.column_stack((source, target)), axis=0, return_index=True)
        distinct = np.sort(distinct)
        indices, source, target = indices[distinct], source[distinct], target[distinct]
        duplicate_count = input_count - len(indices)
        record.update(
            {"distinct_correspondences": len(indices), "duplicate_coordinate_rows": duplicate_count}
        )
        if len(indices) < config.min_inliers:
            reject("too_few_inliers", "insufficient_distinct_correspondences")
            continue
        if _rank(source, scale) < 3 or _rank(target, scale) < 3:
            reject("degenerate", "collinear_or_collapsed_points")
            continue
        native_seed = (seed + cluster.id * 2654435761) & 0x7FFFFFFF
        cv2.setRNGSeed(native_seed)
        estimator = (
            cv2.estimateAffine2D if config.model == "affine" else cv2.estimateAffinePartial2D
        )
        try:
            raw_matrix, raw_mask = estimator(
                np.ascontiguousarray(source, dtype=np.float32),
                np.ascontiguousarray(target, dtype=np.float32),
                method=cv2.RANSAC,
                ransacReprojThreshold=config.reproj_threshold,
                maxIters=config.max_iterations,
                confidence=config.ransac_stop_confidence,
                refineIters=10,
            )
        except cv2.error as error:
            raise InputRejectedError(
                "native RANSAC estimation failed; no verified output produced"
            ) from error
        if raw_matrix is None and raw_mask is None:
            reject("model_not_found")
            continue
        if raw_matrix is None or raw_mask is None:
            raise InputRejectedError("native RANSAC returned inconsistent transform/inlier data")
        matrix = np.asarray(raw_matrix, dtype=np.float64)
        native_inliers = np.asarray(raw_mask).reshape(-1)
        if (
            matrix.shape != (2, 3)
            or not np.isfinite(matrix).all()
            or native_inliers.shape != (len(indices),)
            or native_inliers.dtype.kind not in "bui"
            or np.any((native_inliers != 0) & (native_inliers != 1))
        ):
            raise InputRejectedError("native RANSAC returned invalid geometry or inlier dimensions")
        geometry = _geometry(matrix, config)
        if isinstance(geometry, str):
            reject("degenerate", geometry)
            continue
        estimated_scale, rotation, shear, reflection = geometry
        residual = source @ matrix[:, :2].T + matrix[:, 2] - target
        squared = np.sum(residual * residual, axis=1)
        accepted = (native_inliers != 0) & (squared <= config.reproj_threshold**2)
        count = int(np.count_nonzero(accepted))
        if count < config.min_inliers:
            reject("too_few_inliers")
            continue
        if _rank(source[accepted], scale) < 3 or _rank(target[accepted], scale) < 3:
            reject("degenerate", "collinear_inliers")
            continue
        if float(np.linalg.norm(target[accepted] - source[accepted], axis=1).max()) <= 1e-6:
            reject("trivial_transform")
            continue
        rmse = float(np.sqrt(np.mean(squared[accepted])))
        source_box, target_box = _boxes(
            source[accepted], target[accepted], matrix, features.image_size
        )
        flags, repetition, work = inspect_repetition(
            image,
            source_box,
            matrix,
            config=config,
            remaining_work=config.max_texture_work - texture_work,
        )
        texture_work += work
        score = count / input_count * math.exp(-rmse / config.reproj_threshold)
        if "possible_repetitive_structure" in flags:
            score = min(score, 0.25)
            flagged += 1
        if "insufficient_texture" in flags:
            score = min(score, 0.1)
        if "repetition_check_unavailable" in flags:
            score = min(score, 0.5)
        extra_flags = []
        if duplicate_count:
            extra_flags.append("duplicate_coordinates_removed")
        if reflection:
            extra_flags.append("reflection")
        flags = (f"ransac_{config.model}", *flags, *extra_flags)
        transform = VerifiedTransform(
            cluster_id=cluster.id,
            matrix=matrix,
            inliers=count,
            rmse=rmse,
            scale=estimated_scale,
            rotation_deg=rotation,
            score=score,
            flags=flags,
        )
        transforms.append(transform)
        selected_rows = indices[accepted]
        inlier_clusters.append(
            Cluster(
                id=cluster.id,
                pair_indices=tuple(int(v) for v in selected_rows),
                source_bbox=source_box,
                target_bbox=target_box,
                mean_displacement=(
                    float((target[accepted, 1] - source[accepted, 1]).mean()),
                    float((target[accepted, 0] - source[accepted, 0]).mean()),
                ),
            )
        )
        record.update(
            {
                "status": "verified_geometry",
                "reason": None,
                "native_seed": native_seed,
                "matrix": matrix.tolist(),
                "inliers": count,
                "inlier_ratio": count / input_count,
                "rmse": rmse,
                "scale": estimated_scale,
                "rotation_deg": rotation,
                "shear_deg": shear,
                "score": score,
                "flags": list(flags),
                "repetition": repetition,
            }
        )
        details.append(record)
    verified = Verified(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        cluster_ids=tuple(sorted(c.id for c in clusters.clusters)),
        transforms=tuple(transforms),
    )
    inliers = Clusters(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        num_matches=len(matches.pairs),
        clusters=tuple(inlier_clusters),
    )
    return VerificationOutcome(
        verified,
        inliers,
        {
            "num_input_clusters": len(clusters.clusters),
            "num_verified_clusters": len(transforms),
            "rejection_counts": rejection_counts,
            "repetitive_structure_flags": flagged,
            "ransac_work_bound": ransac_work,
            "texture_work": texture_work,
            "seed": seed,
            "opencv_version": cv2.__version__,
            "opencv_threads": 1,
            "calibration": "uncalibrated",
            "score_kind": "geometric_heuristic",
            "correspondence_policy": "distinct_oriented_xy_pairs",
            "max_heuristic_score": max((t.score for t in transforms), default=None),
            "rotation_convention": "working_xy_positive_clockwise",
            "bbox_policy": "inlier_source_envelope_affine_target",
            "config": config.model_dump(),
        },
        tuple(details),
    )

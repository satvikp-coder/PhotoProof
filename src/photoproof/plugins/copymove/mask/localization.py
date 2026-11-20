"""Dense localization from source-bound verified transforms and original-row inliers."""

from dataclasses import dataclass

import cv2
import numpy as np

from photoproof.contracts.copymove import Clusters, Keypoints, Mask, Matches, Region, Verified
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.mask.correlation import Correlation, affine_correlation
from photoproof.plugins.copymove.mask.schema import Config


@dataclass(frozen=True)
class Localization:
    mask: Mask
    correlation: Correlation
    summary: dict[str, object]


def _oriented(features: Keypoints, pairs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = features.xy[pairs[:, 0]].astype(np.float64)
    b = features.xy[pairs[:, 1]].astype(np.float64)
    flip = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))
    return np.where(flip[:, None], b, a), np.where(flip[:, None], a, b)


def _box(points: np.ndarray, shape: tuple[int, ...], margin: int) -> tuple[int, int, int, int]:
    return (
        max(0, int(np.floor(points[:, 1].min())) - margin),
        max(0, int(np.floor(points[:, 0].min())) - margin),
        min(shape[0], int(np.ceil(points[:, 1].max())) + margin + 1),
        min(shape[1], int(np.ceil(points[:, 0].max())) + margin + 1),
    )


def _selected(
    gray: np.ndarray,
    matrix: np.ndarray,
    points: np.ndarray,
    config: Config,
    pixel_support: np.ndarray,
) -> tuple[np.ndarray, Correlation, tuple[int, int, int, int]]:
    margin = config.dilate_hull_px + config.zncc_window // 2 + 1
    box = _box(points, gray.shape, margin)
    y0, x0, _y1, _x1 = box
    result = affine_correlation(
        gray, matrix, target_box=box, window=config.zncc_window, pixel_support=pixel_support
    )
    hull = np.zeros(result.values.shape, np.uint8)
    vertices = cv2.convexHull(np.rint(points - [x0, y0]).astype(np.int32))
    cv2.fillConvexPoly(hull, vertices, 1)
    domain: np.ndarray = hull
    if config.dilate_hull_px:
        # Euclidean dilation is linear in ROI pixels, independent of radius squared.
        distance = cv2.distanceTransform(1 - hull, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        domain = (distance <= config.dilate_hull_px).astype(np.uint8)
    yy, xx = np.indices(hull.shape, dtype=np.float64)
    inverse = cv2.invertAffineTransform(matrix)
    sx = inverse[0, 0] * (xx + x0) + inverse[0, 1] * (yy + y0) + inverse[0, 2]
    sy = inverse[1, 0] * (xx + x0) + inverse[1, 1] * (yy + y0) + inverse[1, 2]
    valid = (
        result.valid
        & (domain != 0)
        & (np.hypot(sx - xx - x0, sy - yy - y0) >= config.min_displacement_px)
    )
    selected = (valid & (result.values >= config.zncc_threshold)).astype(np.uint8)
    kernel = np.ones((config.morphology_kernel, config.morphology_kernel), np.uint8)
    selected = cv2.morphologyEx(selected, cv2.MORPH_OPEN, kernel)
    selected = cv2.morphologyEx(selected, cv2.MORPH_CLOSE, kernel)
    selected = np.asarray(selected, dtype=np.uint8) & valid.astype(np.uint8)
    _, labels = cv2.connectedComponents(selected, connectivity=8)
    keep = np.unique(labels[(hull != 0) & (selected != 0)])
    selected = np.isin(labels, keep).astype(np.uint8)
    values = np.where(valid, result.values, 0).astype(np.float32)
    return selected, Correlation(values, valid), box


def localize_verified(
    features: Keypoints,
    matches: Matches,
    verified: Verified,
    inliers: Clusters,
    *,
    image: np.ndarray,
    reproj_threshold: float,
    min_inliers: int,
    config: Config | None = None,
) -> Localization:
    """Localize eligible inliers; callers must additionally verify upstream payload bindings.

    Lexicographic source/target roles identify matching patches, not copying chronology.
    A method-negative result cannot establish authenticity.
    """
    cfg = config or Config()
    width, height = features.image_size
    for contract in (matches, verified, inliers):
        if contract.source_sha256 != features.source_sha256 or contract.image_size != (
            width,
            height,
        ):
            raise InputRejectedError("mask inputs differ in source identity or working frame")
    if (
        image.shape[:2] != (height, width)
        or image.ndim not in (2, 3)
        or image.dtype.kind not in "uif"
        or width * height > cfg.max_pixels
        or (image.ndim == 3 and image.shape[2] not in (1, 3, 4))
        or not np.isfinite(image).all()
        or matches.num_keypoints != len(features.xy)
        or inliers.num_matches != len(matches.pairs)
        or not np.isfinite(reproj_threshold)
        or reproj_threshold <= 0
        or min_inliers < 3
        or len(verified.transforms) > 64
    ):
        raise InputRejectedError(
            "mask input dimensions, counts, geometry or pixel budget are invalid"
        )
    groups = {g.id: g for g in inliers.clusters}
    if set(groups) != {t.cluster_id for t in verified.transforms}:
        raise InputRejectedError("mask verified transforms and inlier groups disagree")
    # Budget full-frame inverse warps plus ROI correlation before native image work.
    pixel_work = 0
    target_points: dict[int, np.ndarray] = {}
    for transform in verified.transforms:
        group = groups[transform.cluster_id]
        singular = np.linalg.svd(transform.matrix[:, :2], compute_uv=False)
        if singular.min() < 0.1 - 1e-6 or singular.max() > 10 + 1e-6:
            raise InputRejectedError("mask transform exceeds verifier supported scale range")
        if len(group.pair_indices) != transform.inliers or transform.inliers < min_inliers:
            raise InputRejectedError("mask transform inlier count disagrees with original rows")
        source, target = _oriented(features, matches.pairs[np.array(group.pair_indices)])
        if len(np.unique(np.column_stack((source, target)), axis=0)) != len(source):
            raise InputRejectedError("mask inlier rows repeat physical correspondences")
        if np.linalg.matrix_rank(np.column_stack((source, np.ones(len(source))))) < 3:
            raise InputRejectedError("mask source inliers are geometrically degenerate")
        error = source @ transform.matrix[:, :2].T + transform.matrix[:, 2] - target
        if np.any(np.linalg.norm(error, axis=1) > reproj_threshold + 1e-5):
            raise InputRejectedError("mask transform does not explain its claimed inlier rows")
        margin = cfg.dilate_hull_px + cfg.zncc_window // 2 + 1
        y0, x0, y1, x1 = _box(target, image.shape, margin)
        pixel_work += width * height + (y1 - y0) * (x1 - x0)
        if pixel_work > cfg.max_pixel_work:
            raise InputRejectedError("mask cumulative pixel work exceeds configured budget")
        target_points[transform.cluster_id] = target
    cv2.setNumThreads(1)
    pixel_support = np.ones((height, width), bool)
    color = image
    if image.ndim == 3 and image.shape[2] == 4:
        opaque = float(np.iinfo(image.dtype).max) if image.dtype.kind in "ui" else 1.0
        pixel_support = image[:, :, 3] == opaque
        color = image[:, :, :3]
    gray = color.astype(np.float64)
    gray /= max(float(np.max(np.abs(gray))), 1.0)
    if gray.ndim == 3:
        gray = gray[:, :, 0] if gray.shape[2] == 1 else gray @ np.array([0.2126, 0.7152, 0.0722])
    source_mask = np.zeros((height, width), bool)
    target_mask = np.zeros((height, width), bool)
    correlation = np.zeros((height, width), np.float32)
    valid = np.zeros((height, width), bool)
    for transform in verified.transforms:
        selected, result, box = _selected(
            gray, transform.matrix, target_points[transform.cluster_id], cfg, pixel_support
        )
        y0, x0, y1, x1 = box
        target_mask[y0:y1, x0:x1] |= selected != 0
        current = correlation[y0:y1, x0:x1]
        previous = valid[y0:y1, x0:x1]
        current[:] = np.where(
            result.valid & (~previous | (result.values > current)), result.values, current
        )
        previous |= result.valid
        inverse = cv2.invertAffineTransform(transform.matrix)
        inverse[:, 2] += inverse[:, :2] @ np.array([x0, y0])
        source_mask |= (
            cv2.warpAffine(selected, inverse, (width, height), flags=cv2.INTER_NEAREST) != 0
        )
        transported_valid = (
            cv2.warpAffine(
                result.valid.astype(np.uint8),
                inverse,
                (width, height),
                flags=cv2.INTER_NEAREST,
            )
            != 0
        )
        transported = cv2.warpAffine(
            result.values, inverse, (width, height), flags=cv2.INTER_NEAREST
        )
        correlation[:] = np.where(
            transported_valid & (~valid | (transported > correlation)),
            transported,
            correlation,
        )
        valid |= transported_valid
    ambiguous = source_mask & target_mask
    source_mask &= ~ambiguous
    target_mask &= ~ambiguous
    valid &= ~ambiguous
    correlation[~valid] = 0
    labels = source_mask.astype(np.uint8) + 2 * target_mask.astype(np.uint8)
    regions = []
    for kind, label in (("source", 1), ("target", 2)):
        yy, xx = np.nonzero(labels == label)
        if len(yy):
            regions.append(
                Region(
                    kind=kind,
                    bbox=(int(yy.min()), int(xx.min()), int(yy.max()) + 1, int(xx.max()) + 1),
                    area=len(yy),
                )
            )
    mask = Mask(
        source_sha256=features.source_sha256,
        image_size=(width, height),
        mask=labels,
        regions=tuple(regions),
    )
    immutable_values = np.frombuffer(correlation.tobytes(), np.float32).reshape(labels.shape)
    immutable_valid = np.frombuffer(valid.tobytes(), bool).reshape(labels.shape)
    return Localization(
        mask,
        Correlation(immutable_values, immutable_valid),
        {
            "status": "copy_move_regions_found" if np.any(labels) else "no_copy_move_found",
            "meaning": "regions detected by this method"
            if np.any(labels)
            else "no copy-move detected by this method",
            "calibration": "uncalibrated",
            "score_kind": "local_zncc_similarity",
            "source_area": int(source_mask.sum()),
            "target_area": int(target_mask.sum()),
            "ambiguous_role_pixels": int(ambiguous.sum()),
            "valid_pixels": int(valid.sum()),
            "verified_transforms": len(verified.transforms),
            "correlation_availability": "available" if np.any(valid) else "unavailable",
            "unavailable_reason": None
            if np.any(valid)
            else (
                "no_verified_transforms"
                if not verified.transforms
                else "no_supported_textured_windows"
            ),
            "pixel_work": pixel_work,
            "config": cfg.model_dump(),
        },
    )

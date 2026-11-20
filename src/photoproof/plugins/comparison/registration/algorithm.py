"""Seeded reference-to-target registration with explicit overlap and failure results.

Only normalized working pixels enter this algorithm. No byte decoding, filesystem
access or authenticity interpretation occurs here. Controller integration is separate.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

import cv2
import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.comparison.registration.schema import Config


@dataclass(frozen=True)
class Registration:
    status: Literal["registered", "registration_failed"]
    model: str
    matrix: np.ndarray | None
    aligned_reference: np.ndarray | None
    overlap_mask: np.ndarray | None
    inlier_points: np.ndarray
    quality: Mapping[str, object]
    flags: tuple[str, ...]


def _failure(
    model: str,
    reason: str,
    *,
    flags: tuple[str, ...] = (),
    facts: Mapping[str, object] | None = None,
) -> Registration:
    empty = np.empty((0, 4), np.float32)
    empty.setflags(write=False)
    return Registration(
        "registration_failed",
        model,
        None,
        None,
        None,
        empty,
        MappingProxyType({"reason": reason, **(facts or {})}),
        flags,
    )


def _admit(image: np.ndarray, *, name: str, maximum: int) -> None:
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim not in {2, 3}
        or min(image.shape[:2]) < 1
        or (image.ndim == 3 and image.shape[2] not in {3, 4})
    ):
        raise InputRejectedError(
            f"registration {name} must be nonempty uint8 L/RGB/RGBA working pixels"
        )
    if image.shape[0] * image.shape[1] > maximum:
        raise InputRejectedError(
            f"registration {name} exceeds its {maximum}-pixel allocation budget"
        )


def _gray(image: np.ndarray) -> np.ndarray:
    return (
        image
        if image.ndim == 2
        else cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY if image.shape[2] == 4 else cv2.COLOR_RGB2GRAY)
    )


def _opaque(image: np.ndarray) -> np.ndarray:
    return (
        image[..., 3] == 255
        if image.ndim == 3 and image.shape[2] == 4
        else np.ones(image.shape[:2], bool)
    )


def _features(image: np.ndarray, cfg: Config) -> tuple[np.ndarray, np.ndarray]:
    height, width = image.shape[:2]
    scale = min(1.0, cfg.max_edge / max(height, width))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    gray = _gray(image)
    opaque = _opaque(image)
    if size != (width, height):
        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        opaque = (
            cv2.resize(opaque.astype(np.float32), size, interpolation=cv2.INTER_AREA) >= 1 - 1e-6
        )
    detector = cv2.SIFT.create(
        nfeatures=cfg.max_features,
        contrastThreshold=cfg.contrast_threshold,
        edgeThreshold=cfg.edge_threshold,
    )
    points, descriptors = detector.detectAndCompute(gray, opaque.astype(np.uint8) * 255)
    if descriptors is None or not points:
        return np.empty((0, 2), np.float32), np.empty((0, 128), np.float32)
    order = sorted(
        range(len(points)),
        key=lambda i: (-points[i].response, points[i].pt, points[i].size, points[i].angle),
    )[: cfg.max_features]
    coordinates = np.asarray([points[i].pt for i in order], np.float64)
    # OpenCV resize maps integer-centered pixels with the half-pixel convention.
    coordinates = (coordinates + 0.5) / np.array([size[0] / width, size[1] / height]) - 0.5
    descriptors = np.asarray(descriptors[order], np.float32)
    if cfg.rootsift:
        descriptors = np.sqrt(
            descriptors / np.maximum(descriptors.sum(axis=1, keepdims=True), 1e-12)
        )
    return coordinates.astype(np.float32), descriptors


def _matches(
    rp: np.ndarray, rd: np.ndarray, tp: np.ndarray, td: np.ndarray, ratio: float
) -> tuple[np.ndarray, np.ndarray]:
    if min(len(rd), len(td)) < 2:
        return rp[:0], tp[:0]
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    forward = [
        a
        for pair in matcher.knnMatch(rd, td, k=2)
        if len(pair) == 2
        for a, b in [pair]
        if a.distance < ratio * b.distance
    ]
    backward = {
        a.queryIdx: a.trainIdx
        for pair in matcher.knnMatch(td, rd, k=2)
        if len(pair) == 2
        for a, b in [pair]
        if a.distance < ratio * b.distance
    }
    rows = []
    seen_r: set[tuple[float, ...]] = set()
    seen_t: set[tuple[float, ...]] = set()
    for match in sorted(forward, key=lambda m: (m.distance, m.queryIdx, m.trainIdx)):
        r, t = tuple(rp[match.queryIdx].tolist()), tuple(tp[match.trainIdx].tolist())
        if backward.get(match.trainIdx) != match.queryIdx or r in seen_r or t in seen_t:
            continue
        seen_r.add(r)
        seen_t.add(t)
        rows.append((match.queryIdx, match.trainIdx))
    return (rp[[r for r, _ in rows]], tp[[t for _, t in rows]])


def _fit(rp: np.ndarray, tp: np.ndarray, *, model: str, cfg: Config) -> np.ndarray | None:
    if model == "homography":
        matrix, _ = cv2.findHomography(
            rp, tp, cv2.RANSAC, cfg.ransac_threshold, maxIters=cfg.max_iterations, confidence=0.99
        )
    else:
        function = cv2.estimateAffinePartial2D if model == "similarity" else cv2.estimateAffine2D
        affine, _ = function(
            rp,
            tp,
            method=cv2.RANSAC,
            ransacReprojThreshold=cfg.ransac_threshold,
            maxIters=cfg.max_iterations,
            confidence=0.99,
            refineIters=10,
        )
        matrix = None if affine is None else np.vstack([affine, [0.0, 0.0, 1.0]])
    return None if matrix is None else np.asarray(matrix, np.float64)


def _project(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    homogeneous = np.column_stack([points, np.ones(len(points))]) @ matrix.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(homogeneous[:, :2] / homogeneous[:, 2:])


def _quality(
    matrix: np.ndarray,
    rp: np.ndarray,
    tp: np.ndarray,
    *,
    reference_size: tuple[int, int],
    target_size: tuple[int, int],
    cfg: Config,
) -> tuple[dict[str, object], np.ndarray, str | None]:
    rw, rh = reference_size
    tw, th = target_size
    invalid = np.zeros(len(rp), bool)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(matrix[2, 2]) < 1e-12:
        return {}, invalid, "nonfinite_or_degenerate_transform"
    matrix = matrix / matrix[2, 2]
    corners = np.array([[0, 0], [rw - 1, 0], [rw - 1, rh - 1], [0, rh - 1]], np.float64)
    denominators = np.column_stack([corners, np.ones(4)]) @ matrix[2]
    if np.min(denominators) < 0.05 or np.max(denominators) / np.min(denominators) > 100:
        return {}, invalid, "projective_pole_or_extreme_perspective"
    normalize_r = np.array([[1 / rw, 0, -0.5], [0, 1 / rh, -0.5], [0, 0, 1]])
    normalize_t = np.array([[1 / tw, 0, -0.5], [0, 1 / th, -0.5], [0, 0, 1]])
    normalized = normalize_t @ matrix @ np.linalg.inv(normalize_r)
    condition = float(np.linalg.cond(normalized))
    determinant = float(np.linalg.det(matrix))
    facts: dict[str, object] = {
        "normalized_condition": condition if math.isfinite(condition) else None,
        "determinant": determinant,
        "reflection": determinant < 0,
    }
    if not math.isfinite(condition) or condition > cfg.max_condition or abs(determinant) < 1e-12:
        return facts, invalid, "ill_conditioned_transform"
    if determinant < 0 and not cfg.allow_reflection:
        return facts, invalid, "reflection_rejected"
    mapped = _project(corners, matrix)
    if (
        not np.isfinite(mapped).all()
        or np.max(np.abs(mapped / np.array([tw, th]))) > 32
        or not cv2.isContourConvex(mapped.astype(np.float32))
    ):
        return facts, invalid, "invalid_projected_footprint"
    error = np.linalg.norm(_project(rp, matrix) - tp, axis=1)
    inliers = np.isfinite(error) & (error <= cfg.ransac_threshold)
    count = int(inliers.sum())
    facts.update(matched_points=len(rp), inliers=count, inlier_ratio=count / len(rp))
    if count < cfg.min_inliers or count / len(rp) < cfg.min_inlier_ratio:
        return facts, inliers, "insufficient_geometric_support"
    rmse = float(np.sqrt(np.mean(error[inliers] ** 2)))
    facts["reprojection_rmse"] = rmse
    for name, points, area in [("reference", rp, rw * rh), ("target", tp, tw * th)]:
        fraction = float(cv2.contourArea(cv2.convexHull(points[inliers].astype(np.float32)))) / area
        facts[f"{name}_support_fraction"] = fraction
        if fraction < cfg.min_support_fraction:
            return facts, inliers, "spatially_degenerate_support"
    return facts, inliers, "excessive_reprojection_error" if rmse > cfg.max_rmse else None


def _warp_reference(
    reference: np.ndarray, target: np.ndarray, *, matrix: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Warp into target pixels; unknown source/alpha/border donors remain invalid."""
    height, width = target.shape[:2]
    aligned = cv2.warpPerspective(
        reference,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    coverage = cv2.warpPerspective(
        _opaque(reference).astype(np.float32),
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    valid = (coverage >= 1 - 1e-6) & _opaque(target)
    inverse = np.linalg.inv(matrix)
    x = np.arange(width, dtype=np.float64)[None, :]
    for start in range(0, height, 128):
        y = np.arange(start, min(start + 128, height), dtype=np.float64)[:, None]
        denominator = inverse[2, 0] * x + inverse[2, 1] * y + inverse[2, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (inverse[0, 0] * x + inverse[0, 1] * y + inverse[0, 2]) / denominator
            v = (inverse[1, 0] * x + inverse[1, 1] * y + inverse[1, 2]) / denominator
        valid[start : start + len(y)] &= (
            np.isfinite(u)
            & np.isfinite(v)
            & (u >= 0)
            & (u <= reference.shape[1] - 1)
            & (v >= 0)
            & (v <= reference.shape[0] - 1)
        )
    aligned[~valid] = 0
    return aligned, valid


def register_pair(
    reference: np.ndarray, target: np.ndarray, *, config: Config | None = None, seed: int = 0
) -> Registration:
    """Estimate actual alignment, or return an explicit failure without aligned pixels."""
    cfg = config or Config()
    _admit(reference, name="reference", maximum=cfg.max_pixels)
    _admit(target, name="target", maximum=cfg.max_pixels)
    if reference.shape[0] * reference.shape[1] + target.shape[0] * target.shape[1] > 8388608:
        raise InputRejectedError("registration exceeds its combined eight-million-pixel budget")
    if type(seed) is not int:
        raise InputRejectedError("registration seed must be an integer")
    if min(*reference.shape[:2], *target.shape[:2]) < 16:
        return _failure(cfg.model, "too_small_for_features")
    cv2.setNumThreads(1)
    cv2.setRNGSeed(seed % 2147483647)
    try:
        rp, rd = _features(reference, cfg)
        tp, td = _features(target, cfg)
        reference_features, target_features = len(rp), len(tp)
        rp, tp = _matches(rp, rd, tp, td, cfg.ratio)
    except cv2.error as error:
        return _failure(
            cfg.model,
            "feature_extraction_or_matching_failed",
            facts={"opencv_error_code": error.code},
        )
    counts = {
        "reference_features": reference_features,
        "target_features": target_features,
        "matched_points": len(rp),
        "seed": seed,
    }
    if len(rp) < cfg.min_inliers:
        return _failure(cfg.model, "insufficient_matches", facts=counts)
    model: str = cfg.model
    flags: tuple[str, ...] = ()
    facts: dict[str, object] = {}
    reason: str | None = "estimator_failed"
    for candidate in (
        [model, "affine"] if model == "homography" and cfg.affine_fallback else [model]
    ):
        try:
            matrix = _fit(rp, tp, model=candidate, cfg=cfg)
            if matrix is None:
                reason = "estimator_failed"
                continue
            facts, inliers, reason = _quality(
                matrix,
                rp,
                tp,
                reference_size=reference.shape[1::-1],
                target_size=target.shape[1::-1],
                cfg=cfg,
            )
        except (cv2.error, np.linalg.LinAlgError):
            reason = "geometric_estimation_failed"
            continue
        if reason is None:
            model = candidate
            flags = ("affine_fallback_from_homography",) if candidate != cfg.model else ()
            break
    else:
        return _failure(cfg.model, reason or "estimator_failed", facts={**counts, **facts})
    if matrix is None:
        return _failure(model, "estimator_failed", facts=counts)
    matrix = matrix / matrix[2, 2]
    aligned, overlap = _warp_reference(reference, target, matrix=matrix)
    ecc_status = "not_requested"
    if cfg.refine_ecc:
        if target.shape[0] * target.shape[1] > 1048576:
            ecc_status = "resource_limit"
        else:
            motion = {
                "homography": cv2.MOTION_HOMOGRAPHY,
                "affine": cv2.MOTION_AFFINE,
                "similarity": cv2.MOTION_EUCLIDEAN,
            }[model]
            delta: np.ndarray = (
                np.eye(3, dtype=np.float32)
                if model == "homography"
                else np.eye(2, 3, dtype=np.float32)
            )
            try:
                _, returned_delta = cv2.findTransformECC(
                    _gray(target),
                    _gray(aligned),
                    delta,
                    motion,
                    (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, cfg.ecc_iterations, 1e-5),
                    inputMask=overlap.astype(np.uint8) * 255,
                    gaussFiltSize=5,
                )
                delta = np.asarray(returned_delta, np.float64)
                if delta.shape == (2, 3):
                    delta = np.vstack([delta, [0, 0, 1]])
                refined = np.linalg.inv(delta) @ matrix
                newer, refined_inliers, rejected = _quality(
                    refined,
                    rp,
                    tp,
                    reference_size=reference.shape[1::-1],
                    target_size=target.shape[1::-1],
                    cfg=cfg,
                )
                if (
                    rejected is None
                    and cast(float, newer["reprojection_rmse"])
                    <= cast(float, facts["reprojection_rmse"]) + 0.05
                ):
                    matrix, facts, inliers = refined / refined[2, 2], newer, refined_inliers
                    aligned, overlap = _warp_reference(reference, target, matrix=matrix)
                    ecc_status = "accepted"
                else:
                    ecc_status = "rejected_by_geometric_validation"
            except (cv2.error, np.linalg.LinAlgError):
                ecc_status = "not_converged"
    if not overlap.any():
        return _failure(model, "no_valid_overlap", facts={**counts, **facts})
    points = np.column_stack([rp[inliers], tp[inliers]]).astype(np.float32)
    for array in (matrix, aligned, overlap, points):
        array.setflags(write=False)
    return Registration(
        "registered",
        model,
        matrix,
        aligned,
        overlap,
        points,
        MappingProxyType(
            {**counts, **facts, "ecc_status": ecc_status, "overlap_fraction": float(overlap.mean())}
        ),
        flags,
    )

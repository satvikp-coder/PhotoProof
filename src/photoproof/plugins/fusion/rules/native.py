"""Measured-footprint fusion: independent families, explicit validity, and bounded attribution."""

import math
from dataclasses import dataclass
from typing import cast

import numpy as np

from photoproof.contracts.evidence_io import SpatialEvidence
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import MAX_FUSED_CELLS, FusedScores
from photoproof.contracts.grids import SamplingGrid, project_scores, validate_pixel_transform
from photoproof.contracts.images import ImageGeometry
from photoproof.plugins.fusion.normalize import DETECTOR_FAMILIES
from photoproof.plugins.fusion.rules.schema import Config


@dataclass(frozen=True)
class FusionOutcome:
    fused: FusedScores | None
    contributions: dict[str, np.ndarray]
    measured: dict[str, np.ndarray]
    strengths: dict[str, str]
    families: dict[str, str]
    reason: str


def fuse_native(
    layers: list[SpatialEvidence],
    *,
    geometry: ImageGeometry,
    config: Config | None = None,
) -> FusionOutcome:
    """Fuse actual sampled evidence without coarsening to the least precise detector.

    Scores and provisional rule thresholds are uncalibrated. Weak/global context
    cannot establish localization or measured independent non-weak coverage.
    """
    cfg = config or Config()
    matrix = validate_pixel_transform(geometry)
    if len(layers) > 32:
        raise InputRejectedError("native fusion exceeds its 32-layer admission ceiling")
    names = [item.layer.detector_id for item in layers]
    if len(set(names)) != len(names):
        raise InputRejectedError(
            "native fusion repeats a detector instead of one family contribution"
        )
    families = {}
    strengths = {}
    steps = []
    nonweak = False
    for item in layers:
        layer = item.layer
        name = layer.detector_id
        if (
            item.source_sha256 != geometry["source_sha256"]
            or layer.calibration != "uncalibrated"
            or name not in DETECTOR_FAMILIES
            or layer.strength_class not in {"weak", "medium", "strong"}
            or not isinstance(layer.map, np.ndarray)
            or layer.map.ndim != 2
            or not layer.map.size
            or layer.map.dtype != np.float32
            or not np.isfinite(layer.map).all()
            or np.any((layer.map < 0) | (layer.map > 1))
            or not isinstance(layer.valid_mask, np.ndarray)
            or layer.valid_mask.dtype != np.bool_
            or layer.valid_mask.shape != layer.map.shape
            or not layer.valid_mask.any()
        ):
            raise InputRejectedError("native fusion has invalid source-bound measured layer data")
        families[name] = DETECTOR_FAMILIES[name]
        strengths[name] = layer.strength_class
        weight = getattr(cfg.strength_weights, layer.strength_class)
        if item.sampling_grid is None:
            if layer.strength_class != "weak" or layer.map.shape != (1, 1):
                raise InputRejectedError("global context cannot become localized non-weak evidence")
            continue
        native_grid = SamplingGrid.from_record(item.sampling_grid.to_record())
        if list(native_grid.image_size) != geometry["decoded_size"]:
            raise InputRejectedError("native fusion sampling frame differs from source geometry")
        stride = np.abs(matrix[:2, :2]) @ np.asarray(native_grid.stride_xy)
        steps.append(stride)
        nonweak |= layer.strength_class != "weak" and weight > 0
    if not steps or not nonweak:
        return FusionOutcome(None, {}, {}, strengths, families, "no_nonweak_measured_coverage")
    finest = np.min(np.asarray(steps), axis=0)
    width, height = geometry["working_size"]
    shape = (math.ceil(height / float(finest[1])), math.ceil(width / float(finest[0])))
    if (
        min(shape) < 1
        or shape[0] > height
        or shape[1] > width
        or shape[0] * shape[1] > MAX_FUSED_CELLS
        or shape[0] * shape[1] * len(layers) > 8 * MAX_FUSED_CELLS
    ):
        raise InputRejectedError(
            "native fusion cannot preserve finest sampling within its grid budget"
        )
    scores = {}
    measured = {}
    for item in layers:
        name = item.layer.detector_id
        native_values = cast(np.ndarray, item.layer.map)  # Validated in the admission pass above.
        if item.sampling_grid is None:
            scores[name] = np.full(shape, float(native_values[0, 0]), np.float32)
            measured[name] = np.zeros(shape, bool)  # Context does not supply measured localization.
        else:
            scores[name], measured[name] = project_scores(
                native_values,
                item.layer.valid_mask,
                grid=item.sampling_grid,
                geometry=geometry,
                output_shape=shape,
                max_output_elements=MAX_FUSED_CELLS,
            )
    weights = {name: float(getattr(cfg.strength_weights, strengths[name])) for name in names}
    strong = {
        name: values * weights[name]
        for name, values in scores.items()
        if strengths[name] == "strong"
    }
    accepted = dict(strong)
    for name, values in scores.items():
        if strengths[name] != "medium":
            continue
        credible = np.zeros(shape, bool)
        for other, supporting in scores.items():
            if (
                families[other] != families[name]
                and strengths[other] != "weak"
                and weights[other] > 0
            ):
                credible |= measured[other] & (supporting >= cfg.corroboration_floor)
        accepted[name] = np.where(
            measured[name] & (credible | (values > cfg.medium_high_score_threshold)),
            values * weights[name],
            0,
        ).astype(np.float32)
    support = np.zeros(shape, np.float32)
    valid = np.zeros(shape, bool)
    for name, values in accepted.items():
        support = np.maximum(support, values)
        if weights[name] > 0:
            valid |= measured[name]
    for name, values in scores.items():
        if strengths[name] == "weak":
            accepted[name] = np.where(
                support > 0,
                values * weights[name] * cfg.weak_boost_factor * support,
                0,
            ).astype(np.float32)
    strongest = np.zeros(shape, np.float32)
    for values in strong.values():
        strongest = np.maximum(strongest, values)
    product = np.ones(shape, np.float32)
    for family in sorted(set(families.values())):
        has_strong = np.zeros(shape, bool)
        other_max = np.zeros(shape, np.float32)
        for name, values in accepted.items():
            if families[name] == family:
                if strengths[name] == "strong":
                    has_strong |= values > 0
                else:
                    other_max = np.maximum(other_max, values)
        product *= 1 - np.where(has_strong, 0, other_max)
        for name, contribution in accepted.items():
            if families[name] == family:
                eligible = (
                    contribution == strongest
                    if strengths[name] == "strong"
                    else (~has_strong & (contribution == other_max))
                )
                accepted[name] = np.where(eligible, contribution, 0).astype(np.float32)
    fused = np.where(valid, np.clip(1 - (1 - strongest) * product, 0, 1), 0).astype(np.float32)
    if not valid.any():
        return FusionOutcome(
            None, accepted, measured, strengths, families, "no_common_measured_coverage"
        )
    return FusionOutcome(
        FusedScores(fused, valid, geometry["source_sha256"], (width, height)),
        accepted,
        measured,
        strengths,
        families,
        "available",
    )

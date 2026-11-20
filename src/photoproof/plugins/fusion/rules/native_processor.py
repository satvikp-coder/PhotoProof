"""Binary fusion entry point for coordinated producer/consumer activation."""

import re
from typing import cast

from photoproof.contracts.contributions import Contribution, write_contributions
from photoproof.contracts.evidence_io import read_evidence_layer
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import MAX_FUSED_CELLS, write_fused_scores
from photoproof.contracts.grids import validate_pixel_transform
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize import DETECTOR_NORMALIZATION, NATIVE_OUTPUTS
from photoproof.plugins.fusion.rules.native import fuse_native
from photoproof.plugins.fusion.rules.schema import Config


def run_native(ctx: PluginContext) -> dict[str, object]:
    """Read real typed layers, apply family gates, and persist exact accepted contributions."""
    config = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )
    records = ctx.inputs.get("fusion.layers", [])
    unavailable = ctx.inputs.get("fusion.unavailable", [])
    if (
        not isinstance(records, list)
        or len(records) > len(NATIVE_OUTPUTS)
        or not isinstance(unavailable, (list, tuple))
        or len(unavailable) > len(NATIVE_OUTPUTS)
        or any(not isinstance(n, str) or n not in NATIVE_OUTPUTS for n in unavailable)
        or len(set(unavailable)) != len(unavailable)
    ):
        raise InputRejectedError(
            "native fusion requires a bounded known layer and availability listing"
        )
    score_info: dict[str, object] = {
        "label": "heuristic_score",
        "calibration": "uncalibrated",
        "rule_version": "3.0.0",
        "parameters": config.model_dump(),
        "unavailable_detectors": list(unavailable),
    }
    geometry_value = ctx.inputs.get("image.geometry")
    if geometry_value is None:
        if records:
            raise InputRejectedError("native fusion cannot align layers without host geometry")
        return {
            "fusion.map": None,
            "fusion.evidence": None,
            "fusion.score_info": {
                **score_info,
                "status": "unavailable",
                "reason": "geometry_unavailable",
            },
        }
    if not isinstance(geometry_value, dict):
        raise InputRejectedError("native fusion requires exact source-bound host geometry")
    geometry = cast(ImageGeometry, geometry_value)
    validate_pixel_transform(geometry)
    layers = []
    admitted = set()
    native_cells = 0
    ranks = {"weak": 0, "medium": 1, "strong": 2}
    for ordinal, record in enumerate(records):
        if not isinstance(record, dict) or record.get("detector_id") not in NATIVE_OUTPUTS:
            raise InputRejectedError(
                "native fusion layer does not name a supported detector contract"
            )
        name = record["detector_id"]
        if name in admitted or name in unavailable:
            raise InputRejectedError("native fusion repeats or contradicts detector availability")
        admitted.add(name)
        evidence = read_evidence_layer(
            ctx, "fusion.layers", ordinal=ordinal, source_sha256=geometry["source_sha256"]
        )
        if (
            ranks[evidence.layer.strength_class]
            > ranks[str(DETECTOR_NORMALIZATION[name]["strength_class"])]
        ):
            raise InputRejectedError("native fusion layer exceeds its documented strength ceiling")
        if name == "copymove.mask":
            notes = evidence.layer.notes
            if (
                "verified_dense_pixel_similarity" not in notes
                or "original_source_bound" not in notes
                or not any(re.fullmatch(r"mask_sha256:[0-9a-f]{64}", note) for note in notes)
                or not any(
                    re.fullmatch(r"verification_bindings_sha256:[0-9a-f]{64}", note)
                    for note in notes
                )
            ):
                raise InputRejectedError(
                    "copy-move fusion requires normalized verified-mask provenance"
                )
        native_cells += evidence.layer.valid_mask.size
        if native_cells > 8 * MAX_FUSED_CELLS:
            raise InputRejectedError(
                "native fusion source grids exceed the cumulative array budget"
            )
        layers.append(evidence)
    result = fuse_native(layers, geometry=geometry, config=config)
    if result.fused is None:
        return {
            "fusion.map": None,
            "fusion.evidence": None,
            "fusion.score_info": {**score_info, "status": "unavailable", "reason": result.reason},
        }
    entries = tuple(
        Contribution(
            layer.layer.detector_id,
            result.families[layer.layer.detector_id],
            layer.layer.strength_class,
            float(getattr(config.strength_weights, layer.layer.strength_class)),
            "global_context" if layer.sampling_grid is None else "spatial",
            tuple(layer.layer.notes),
            result.contributions[layer.layer.detector_id],
            result.measured[layer.layer.detector_id],
        )
        for layer in layers
    )
    fused_ref = write_fused_scores(ctx, result.fused)
    contributions = write_contributions(
        ctx, entries, fused=result.fused, unavailable=tuple(unavailable)
    )
    return {
        "fusion.map": fused_ref,
        "fusion.evidence": contributions,
        "fusion.score_info": {
            **score_info,
            "status": "available",
            "reason": result.reason,
            "source_sha256": result.fused.source_sha256,
            "working_size": list(result.fused.working_size),
            "grid_shape": list(result.fused.scores.shape),
        },
    }

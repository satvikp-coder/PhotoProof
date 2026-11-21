"""Actual measured comparison-to-region controller integration."""

from typing import cast

from photoproof.contracts.arrays import encode_npy
from photoproof.contracts.changes import DifferenceRecord, encode_change_mask, similarity_binding
from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.similarity import read_similarity
from photoproof.plugins.comparison.change_mask.algorithm import extract_changes
from photoproof.plugins.comparison.change_mask.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    target = ctx.inputs.get("image.geometry")
    reference = ctx.inputs.get("image.reference_geometry")
    if not isinstance(target, dict) or not isinstance(reference, dict):
        raise InputRejectedError("change extraction requires both source geometries")
    measured = read_similarity(
        ctx,
        target_geometry=cast(ImageGeometry, target),
        reference_geometry=cast(ImageGeometry, reference),
    )
    if measured is None:
        raise NotApplicableError("change extraction has no measured similarity maps")
    cfg = ctx.config if isinstance(ctx.config, Config) else Config.model_validate(ctx.config or {})
    result = extract_changes(measured.dissimilarity, measured.valid_mask, config=cfg)
    if result.labels is None:
        raise InputRejectedError("change extraction did not produce actual component labels")
    labels_bytes = encode_npy(result.labels, max_elements=4194304)
    mask_bytes = encode_change_mask(result.mask)
    labels = ctx.write_artifact("change_labels.npy", labels_bytes, "application/x-npy")
    mask = ctx.write_artifact("change_mask.png", mask_bytes, "image/png")
    raw = {
        "format_version": 1,
        "coordinate_convention": "zero_based_pixel_centres_xy",
        "target_sha256": target["source_sha256"],
        "reference_sha256": reference["source_sha256"],
        "target_size": target["working_size"],
        "similarity_binding": similarity_binding(ctx.inputs["comparison.similarity_summary"]),
        "regions": [dict(r) for r in result.regions],
        "facts": dict(result.summary),
        "mask": mask,
        "labels": labels,
    }
    record = DifferenceRecord.model_validate(raw)
    output = record.model_dump(mode="json", exclude={"mask", "labels"})
    output.update(mask=mask, labels=labels)
    return {
        "comparison.change_mask": mask,
        "comparison.change_labels": labels,
        "comparison.change_regions": output,
    }

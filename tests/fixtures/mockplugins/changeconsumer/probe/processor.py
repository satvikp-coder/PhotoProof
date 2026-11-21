from typing import cast

from photoproof.contracts.changes import read_changes
from photoproof.contracts.exceptions import NotApplicableError
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    result = read_changes(
        ctx,
        target_geometry=cast(ImageGeometry, ctx.inputs["image.geometry"]),
        reference_geometry=cast(ImageGeometry, ctx.inputs["image.reference_geometry"]),
    )
    if result is None:
        raise NotApplicableError("measured changes unavailable")
    record, labels = result
    return {
        "comparison.change_probe": {
            "shape": list(labels.shape),
            "changed_pixels": int((labels > 0).sum()),
            "read_only": not labels.flags.writeable,
            "status": record.facts["status"],
        }
    }

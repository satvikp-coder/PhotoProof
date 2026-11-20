"""Render measured working-frame evidence on a hash/source-matched safe preview."""

import io
from hashlib import sha256
from typing import cast

import numpy as np
from PIL import Image

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import read_fused_scores
from photoproof.contracts.grids import validate_pixel_transform
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.heatmap.native_renderer import render_native
from photoproof.plugins.fusion.heatmap.schema import Config


def _derived_png(data: bytes) -> None:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise InputRejectedError("heatmap safe preview must be a derived PNG")
    offset = 8
    chunks: list[bytes] = []
    while offset < len(data):
        if offset + 12 > len(data) or len(chunks) >= 4096:
            raise InputRejectedError("heatmap derived PNG chunk bounds are invalid")
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        if offset + size + 12 > len(data) or kind not in {b"IHDR", b"IDAT", b"IEND"}:
            raise InputRejectedError(
                "heatmap derived PNG contains invalid or unexpected metadata chunks"
            )
        chunks.append(kind)
        offset += size + 12
    if (
        not chunks
        or chunks[0] != b"IHDR"
        or chunks[-1] != b"IEND"
        or chunks.count(b"IHDR") != 1
        or chunks.count(b"IEND") != 1
        or b"IDAT" not in chunks
    ):
        raise InputRejectedError("heatmap derived PNG framing is invalid")


def run_native(ctx: PluginContext) -> dict[str, object]:
    config = (
        ctx.config
        if isinstance(ctx.config, Config)
        else Config.model_validate({} if ctx.config is None else ctx.config)
    )
    legend: dict[str, object] = {
        "colormap": "cividis",
        "range": [0, 1],
        "label": "heuristic suspicion score (uncalibrated)",
        "calibration": "uncalibrated",
        "display_threshold": config.display_threshold,
        "opacity": config.opacity,
        "notes": [
            "Uncalibrated heuristic; requires corroboration and expert interpretation.",
            "Thresholds are provisional until calibration (#37).",
            "Unknown or below-threshold cells have transparent heat and derived gray context.",
        ],
    }
    if ctx.inputs.get("fusion.map") is None:
        return {
            "fusion.heatmap": None,
            "fusion.overlay": None,
            "fusion.outlines": None,
            "fusion.legend": {
                **legend,
                "status": "unavailable",
                "message": "no fused evidence available",
            },
        }
    raw_geometry = ctx.inputs.get("image.geometry")
    if not isinstance(raw_geometry, dict):
        raise InputRejectedError("heatmap requires source-bound working geometry")
    geometry = cast(ImageGeometry, raw_geometry)
    validate_pixel_transform(geometry)
    fused = read_fused_scores(ctx, geometry=geometry)
    if fused is None:
        raise InputRejectedError("heatmap expected measured fused scores")
    info = ctx.inputs.get("support.preview_info")
    raw_ref = ctx.inputs.get("support.preview")
    if not isinstance(info, dict) or not isinstance(raw_ref, dict):
        raise InputRejectedError("heatmap safe preview is unavailable; preserve existing evidence")
    ref = ArtifactRef.model_validate(raw_ref.get("artifact"))
    if (
        type(info.get("format_version")) is not int
        or info["format_version"] != 2
        or info.get("source_sha256") != fused.source_sha256
        or info.get("working_size") != list(fused.working_size)
        or info.get("coordinate_space") != "working_pixels"
        or info.get("kind") != "derived_preview"
        or info.get("evidence") is not False
        or info.get("preview_sha256") != ref.sha256
        or ref.producer != "support.preview"
        or ref.media_type != "image/png"
    ):
        raise InputRejectedError(
            "heatmap preview source, coordinates or derived-artifact provenance differs"
        )
    edge, width, height = info.get("max_edge"), info.get("width"), info.get("height")
    if any(type(n) is not int for n in (edge, width, height)):
        raise InputRejectedError("heatmap preview dimensions and limit must be integer pixels")
    edge, width, height = cast(int, edge), cast(int, width), cast(int, height)
    scale = min(1.0, edge / max(fused.working_size))
    expected = tuple(max(1, int(n * scale)) for n in fused.working_size)
    if not 1 <= edge <= 4096 or (width, height) != expected:
        raise InputRejectedError(
            "heatmap preview dimensions differ from its bounded source sampling"
        )
    data = ctx.artifact("support.preview")
    if sha256(data).hexdigest() != ref.sha256:
        raise InputRejectedError("heatmap safe preview hash mismatch; no replacement is generated")
    _derived_png(data)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != "PNG"
                or image.mode not in {"L", "RGB", "RGBA"}
                or image.size != expected
                or getattr(image, "n_frames", 1) != 1
            ):
                raise InputRejectedError(
                    "heatmap derived preview header disagrees with source bounds"
                )
            preview = np.array(image)
    except (OSError, ValueError) as error:
        raise InputRejectedError(
            "heatmap derived preview is invalid; preserve the artifact"
        ) from error
    summary = ctx.inputs.get("fusion.segmentation")
    regions = ctx.inputs.get("fusion.regions", [])
    if summary is not None:
        if (
            not isinstance(summary, dict)
            or summary.get("source_sha256") != fused.source_sha256
            or summary.get("working_size") != list(fused.working_size)
            or summary.get("grid_shape") != list(fused.scores.shape)
            or summary.get("coordinate_space") != "uniform_working_image_cells"
            or summary.get("calibration") != "uncalibrated"
        ):
            raise InputRejectedError(
                "heatmap region metadata differs from its source-bound fused grid"
            )
        legend["region_metadata_truncated"] = summary.get("truncated") is True
    elif regions:
        raise InputRejectedError("heatmap boundary coordinates have no source-frame provenance")
    heat, overlay, outlines = render_native(
        fused.scores, fused.valid_mask, preview, regions=regions, config=config
    )
    # Both nested references were verified by read_fused_scores through the host broker.
    record = cast(dict[str, dict[str, object]], ctx.inputs["fusion.map"])
    score_ref = ArtifactRef.model_validate(record["scores"]["artifact"])
    valid_ref = ArtifactRef.model_validate(record["valid_mask"]["artifact"])
    legend.update(
        status="rendered",
        source_sha256=fused.source_sha256,
        working_size=list(fused.working_size),
        grid_shape=list(fused.scores.shape),
        preview_sha256=ref.sha256,
        fused_scores_sha256=score_ref.sha256,
        fused_validity_sha256=valid_ref.sha256,
        dimensions=list(preview.shape[:2]),
        region_status="available" if summary is not None else "unavailable",
    )
    return {
        "fusion.heatmap": ctx.write_artifact("heuristic-heatmap.png", heat, "image/png"),
        "fusion.overlay": ctx.write_artifact("derived-overlay.png", overlay, "image/png"),
        "fusion.outlines": ctx.write_artifact("region-outlines.png", outlines, "image/png"),
        "fusion.legend": legend,
    }

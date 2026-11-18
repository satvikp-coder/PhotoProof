"""Deterministic rendering, reference lookup, transparency and unavailable evidence."""

import hashlib
import io
import logging

import cv2
import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.heatmap.colormap import CIVIDIS
from photoproof.plugins.fusion.heatmap.processor import render, run
from photoproof.plugins.fusion.heatmap.schema import Config
from photoproof.plugins.support.preview.algorithm import encode_preview


def test_lookup_matches_opencv_reference():
    reference = cv2.applyColorMap(
        np.arange(256, dtype=np.uint8).reshape(256, 1), cv2.COLORMAP_CIVIDIS
    )[:, 0, ::-1]
    assert np.array_equal(CIVIDIS, reference)
    assert not CIVIDIS.flags.writeable


def test_render_determinism_scaling_and_transparency():
    scores = np.array([[0.0, 0.9], [0.2, 0.8]], dtype=np.float32)
    preview = np.full((16, 24, 3), 120, dtype=np.uint8)
    regions = [{"polygon": [[1, 0], [2, 0], [2, 2], [1, 2]]}]
    first = render(scores, preview, regions=regions, config=Config())
    assert first == render(scores, preview, regions=regions, config=Config())
    arrays = []
    for data in first:
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (24, 16) and not image.info
            arrays.append(np.array(image))
    heat, overlay, outlines = arrays
    assert np.all(heat[:, :12, 3] == 0)
    assert np.all(heat[:, 12:, 3] == 255)
    assert np.all(overlay[:, :12] == 120)
    assert outlines[..., 3].any()
    assert not outlines[:, :12, 3].any()


def _context(scores, *, bad_hash=False):
    pixels = np.full((12, 12, 3), 100, dtype=np.uint8)
    data, _, _ = encode_preview(pixels)
    ref = ArtifactRef(
        name="derived-preview.png",
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        media_type="image/png",
        producer="support.preview",
    )
    dumped = ref.model_dump()
    if bad_hash:
        dumped["sha256"] = "0" * 64
    written = []

    def writer(name, data, media_type):
        written.append(data)
        return ArtifactRef(
            name=name,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            media_type=media_type,
            producer="fusion.heatmap",
        )

    ctx = PluginContext(
        run_id="r",
        plugin_id="fusion.heatmap",
        inputs={
            "fusion.map": scores,
            "fusion.regions": [],
            "support.preview": {"artifact": dumped},
            "support.preview_info": {"max_edge": 2048, "width": 12, "height": 12},
        },
        config=Config(),
        seed=0,
        write_artifact=writer,
        logger=logging.getLogger("test"),
        read_artifact=lambda name, limit, selector: data,
    )
    return ctx, written


@pytest.mark.parametrize("value", [None, []])
def test_no_map_produces_no_artifacts(value):
    ctx, written = _context(value)
    result = run(ctx)
    assert not written
    assert all(
        result[name] is None for name in ("fusion.heatmap", "fusion.overlay", "fusion.outlines")
    )
    assert result["fusion.legend"]["message"] == "no fused evidence available"
    assert result["fusion.legend"]["status"] == "unavailable"


def test_actual_artifacts_and_legend_are_deterministic():
    ctx, written = _context([[0.9, 0.0], [0.9, 0.1]])
    first = run(ctx)
    assert first == run(ctx)
    assert len(written) == 6
    assert written[:3] == written[3:]
    assert "uncalibrated" in first["fusion.legend"]["label"]
    assert (
        first["fusion.legend"]["preview_sha256"]
        == ctx.inputs["support.preview"]["artifact"]["sha256"]
    )


def test_preview_mismatch_fails_visibly():
    ctx, written = _context([[0.9]], bad_hash=True)
    with pytest.raises(InputRejectedError, match="hash mismatch"):
        run(ctx)
    assert not written


@pytest.mark.parametrize("value", [[[float("nan")]], [[1.1]], [0.8]])
def test_invalid_scores_fail_visibly(value):
    ctx, written = _context(value)
    with pytest.raises(InputRejectedError, match="finite 2-D"):
        run(ctx)
    assert not written


@pytest.mark.parametrize("field,value", [("width", 11), ("height", 13), ("max_edge", 8)])
def test_preview_header_must_match_declared_bounds(field, value):
    ctx, written = _context([[0.9]])
    ctx.inputs["support.preview_info"][field] = value
    with pytest.raises(InputRejectedError, match="header"):
        run(ctx)
    assert not written

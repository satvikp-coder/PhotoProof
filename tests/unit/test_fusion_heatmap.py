"""Deterministic rendering, reference lookup, transparency and unavailable evidence."""

import hashlib
import io
import logging

import cv2
import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.arrays import encode_npy
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.heatmap.colormap import CIVIDIS
from photoproof.plugins.fusion.heatmap.processor import render, run
from photoproof.plugins.fusion.heatmap.schema import Config
from photoproof.plugins.support.preview.algorithm import encode_preview
from tests.unit.test_score_grid_projection import _geometry


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
    pixels = np.full((12, 12, 3), 100, np.uint8)
    data, _, _ = encode_preview(pixels)
    blobs = {}

    def reference(name, payload, producer):
        ref = ArtifactRef(
            name=name,
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
            media_type="image/png" if name.endswith(".png") else "application/x-npy",
            producer=producer,
        )
        blobs[ref.sha256] = payload
        return {"artifact": ref.model_dump()}

    preview_ref = reference("derived-preview.png", data, "support.preview")
    if bad_hash:
        preview_ref["artifact"]["sha256"] = "0" * 64
        blobs["0" * 64] = data  # Fault injection exercises the consumer's independent hash check.
    value = scores
    if scores is not None and not (isinstance(scores, list) and not scores):
        values = np.asarray(scores, np.float32)
        valid = np.ones(values.shape, bool)
        value = {
            "format_version": 1,
            "source_sha256": "a" * 64,
            "working_size": [12, 12],
            "grid_shape": list(values.shape),
            "coordinate_space": "uniform_working_image_cells",
            "calibration": "uncalibrated",
            "scores": reference("scores.npy", encode_npy(values), "fusion.rules"),
            "valid_mask": reference("valid.npy", encode_npy(valid), "fusion.rules"),
        }
    inputs = {
        "image.geometry": _geometry(12, 12),
        "fusion.map": value,
        "fusion.regions": [],
        "support.preview": preview_ref,
        "support.preview_info": {
            "format_version": 2,
            "source_sha256": "a" * 64,
            "working_size": [12, 12],
            "coordinate_space": "working_pixels",
            "kind": "derived_preview",
            "evidence": False,
            "preview_sha256": preview_ref["artifact"]["sha256"],
            "max_edge": 2048,
            "width": 12,
            "height": 12,
        },
    }
    written = []

    def writer(name, payload, media_type):
        written.append(payload)
        return ArtifactRef(
            name=name,
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
            media_type=media_type,
            producer="fusion.heatmap",
        )

    def reader(name, limit, selector):
        node = inputs[name]
        for step in selector:
            node = node[step]
        payload = blobs[node["artifact"]["sha256"]]
        assert len(payload) <= limit
        return payload

    return PluginContext(
        run_id="r",
        plugin_id="fusion.heatmap",
        inputs=inputs,
        config=Config(),
        seed=0,
        write_artifact=writer,
        logger=logging.getLogger(__name__),
        read_artifact=reader,
    ), written


@pytest.mark.parametrize("value", [None, []])
def test_no_map_produces_no_artifacts(value):
    ctx, written = _context(value)
    if value == []:
        with pytest.raises(InputRejectedError):
            run(ctx)
        assert not written
        return
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
    with pytest.raises(InputRejectedError, match=r"finite|score|grid|dimensions"):
        run(ctx)
    assert not written


@pytest.mark.parametrize("field,value", [("width", 11), ("height", 13), ("max_edge", 8)])
def test_preview_header_must_match_declared_bounds(field, value):
    ctx, written = _context([[0.9]])
    ctx.inputs["support.preview_info"][field] = value
    with pytest.raises(InputRejectedError, match=r"dimensions|sampling"):
        run(ctx)
    assert not written

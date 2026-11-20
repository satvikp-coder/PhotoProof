"""Actual image ingestion through native evidence, fusion, localization and heatmap."""

import io
import json

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npy
from photoproof.core.controller import AnalysisController


@pytest.mark.parametrize("orientation", [1, 6])
def test_actual_copy_reaches_binary_fusion_and_source_matched_heatmap(
    make_app, tmp_path, orientation
):
    pixels = np.full((384, 512, 3), 120, np.uint8)
    patch = np.random.default_rng(22).integers(0, 256, (96, 96, 3), np.uint8)
    pixels[32:128, 32:128] = patch
    pixels[240:336, 240:336] = patch
    exif = Image.Exif()
    exif[274] = orientation
    path = tmp_path / "generated-copy.png"
    Image.fromarray(pixels).save(path, exif=exif)
    original = path.read_bytes()
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    controller = AnalysisController(app)
    result = controller.analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    for plugin in (
        "noise.variance_map",
        "copymove.mask",
        "fusion.normalize",
        "fusion.rules",
        "fusion.assessment",
        "fusion.regions",
        "fusion.heatmap",
    ):
        assert states[plugin] == "SUCCESS", result.plugins
    assert result.status.value == "COMPLETED", result.plugins
    layers = result.outputs["fusion.layers"]
    assert layers and all(layer["format_version"] == 2 for layer in layers)
    assert len(json.dumps(layers)) < 16_384
    assert all(layer["source_sha256"] == target.sha256 for layer in layers)
    assert all(layer["calibration"] == "uncalibrated" for layer in layers)
    copy = next(layer for layer in layers if layer["detector_id"] == "copymove.mask")
    assert copy["strength_class"] == "strong"
    fused = result.outputs["fusion.map"]
    assert fused["source_sha256"] == target.sha256
    expected_shape = (512, 384) if orientation == 6 else (384, 512)
    assert fused["grid_shape"] == list(expected_shape)

    def payload(ref):
        return app.storage.blob_path("artifacts", ref["artifact"]["sha256"]).read_bytes()

    values = decode_npy(payload(fused["scores"]), expected_ndim=2)
    valid = decode_npy(payload(fused["valid_mask"]), expected_ndim=2)
    assert values.shape == valid.shape == expected_shape
    assert values.max() > 0.7 and not values[~valid].any()
    assert result.outputs["fusion.assessment"]["category"] == "manipulation_indicated"
    assert result.outputs["fusion.regions"]
    assert all(region["contributing_detectors"] for region in result.outputs["fusion.regions"])
    with Image.open(io.BytesIO(payload(result.outputs["fusion.mask"]))) as image:
        mask = np.array(image) != 0
    assert mask.any() and not mask[~valid].any()
    expected_mask = np.zeros((384, 512), bool)
    expected_mask[32:128, 32:128] = expected_mask[240:336, 240:336] = True
    if orientation == 6:
        expected_mask = np.rot90(expected_mask, k=-1)
    iou = np.count_nonzero(mask & expected_mask) / np.count_nonzero(mask | expected_mask)
    print(f"native pipeline orientation={orientation} union IoU={iou:.6f}")
    assert iou >= 0.7
    with Image.open(io.BytesIO(payload(result.outputs["fusion.heatmap"]))) as image:
        heat = np.array(image)
        assert not image.info
    assert heat.shape == (*expected_shape, 4) and not heat[..., 3][~valid].any()
    legend = result.outputs["fusion.legend"]
    assert legend["source_sha256"] == target.sha256
    assert legend["preview_sha256"] == result.outputs["support.preview"]["artifact"]["sha256"]
    assert legend["fused_scores_sha256"] == fused["scores"]["artifact"]["sha256"]
    assert legend["fused_validity_sha256"] == fused["valid_mask"]["artifact"]["sha256"]
    assert "verdict" not in result.model_dump()
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original
    assert path.read_bytes() == original

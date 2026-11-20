"""Real uploads, isolated decoding, SIFT, verified inliers, and dense mask artifacts."""

import json
from hashlib import sha256

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.copymove import Mask
from photoproof.core.controller import AnalysisController
from tools.metrics import iou


@pytest.mark.parametrize("kind", ["translation", "rotation", "scale"])
def test_actual_controller_dense_mask_and_metrics(make_app, tmp_path, kind):
    side = 96 if kind == "translation" else 128
    patch = np.random.default_rng(22).integers(0, 256, (side, side, 3), np.uint8)
    copied = Image.fromarray(patch)
    if kind == "rotation":
        copied = copied.rotate(30, resample=Image.Resampling.BICUBIC, expand=True)
    elif kind == "scale":
        copied = copied.resize((102, 102), Image.Resampling.BICUBIC)
    pixels = np.full((512, 512, 3), 120, np.uint8)
    pixels[32 : 32 + side, 32 : 32 + side] = patch
    pixels[240 : 240 + copied.height, 240 : 240 + copied.width] = np.asarray(copied)
    expected = np.zeros((512, 512), np.uint8)
    expected[32 : 32 + side, 32 : 32 + side] = 1
    expected[240 : 240 + copied.height, 240 : 240 + copied.width] = 2
    path = tmp_path / "controlled-copy.png"
    Image.fromarray(pixels).save(path)
    original = path.read_bytes()
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    controller = AnalysisController(app)
    result = controller.analyze(AnalysisRequest(target=target, use_cache=False))
    assert {p.plugin_id: p.state for p in result.plugins}["copymove.mask"] == "SUCCESS", (
        result.plugins
    )

    def payload(name):
        return app.storage.blob_path(
            "artifacts", result.outputs[name]["artifact"]["sha256"]
        ).read_bytes()

    mask = Mask.from_png(
        payload("copymove.mask"),
        metadata=payload("copymove.mask_metadata").decode(),
        source_sha256=target.sha256,
    )
    correlation = decode_npy(payload("copymove.correlation_map"), expected_ndim=2)
    validity = decode_npy(payload("copymove.correlation_validity"), expected_ndim=2)
    regions = json.loads(payload("copymove.regions"))
    assert mask.image_size == (512, 512)
    assert correlation.dtype == np.float32 and validity.dtype == bool
    assert correlation.shape == validity.shape == mask.mask.shape
    assert np.all(np.abs(correlation) <= 1) and not correlation[~validity].any()
    for field, name in [
        ("mask_sha256", "copymove.mask"),
        ("mask_metadata_sha256", "copymove.mask_metadata"),
        ("correlation_sha256", "copymove.correlation_map"),
        ("validity_sha256", "copymove.correlation_validity"),
        ("verification_bindings_sha256", "copymove.verification_bindings"),
    ]:
        assert regions[field] == sha256(payload(name)).hexdigest()
    assert sum(r["area_pixels"] for r in regions["regions"]) == np.count_nonzero(mask.mask)
    assert regions["coordinate_space"] == "working_pixels"
    assert regions["calibration"] == "uncalibrated"
    baseline = iou(expected.tolist(), mask.mask.tolist())
    print(f"actual controller default mask {kind} union IoU={baseline:.6f}")
    if kind == "translation":
        assert baseline >= 0.7
    else:
        assert np.any(mask.mask == 1) and np.any(mask.mask == 2)
    assert "verdict" not in result.model_dump()
    repeated = controller.analyze(AnalysisRequest(target=target, use_cache=False))
    for name in (
        "copymove.mask",
        "copymove.mask_metadata",
        "copymove.correlation_map",
        "copymove.correlation_validity",
        "copymove.regions",
    ):
        assert (
            repeated.outputs[name]["artifact"]["sha256"]
            == result.outputs[name]["artifact"]["sha256"]
        )
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original


def test_actual_no_copy_result_is_method_negative(make_app, tmp_path):
    path = tmp_path / "flat.png"
    Image.fromarray(np.full((64, 64, 3), 120, np.uint8)).save(path)
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert {p.plugin_id: p.state for p in result.plugins}["copymove.mask"] == "SUCCESS", (
        result.plugins
    )
    assert result.outputs["copymove.mask_stats"]["status"] == "no_copy_move_found"
    assert result.outputs["copymove.mask_stats"]["valid_pixels"] == 0
    assert "authentic" not in str(result.outputs["copymove.mask_stats"])

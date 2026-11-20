"""Actual registered detectors produce source-bound numerical evidence through the controller."""

import numpy as np

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.core.controller import AnalysisController
from tests.fixtures.native_images import write_copy_image


def test_fusion_normalize_runs_with_available_detectors(make_app, tmp_path):
    app = make_app("photoproof.plugins")
    path = write_copy_image(tmp_path / "copy.png")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert {p.plugin_id: p.state for p in result.plugins}["fusion.normalize"] == "SUCCESS"
    layers = result.outputs["fusion.layers"]
    assert layers
    for layer in layers:
        assert layer["calibration"] == "uncalibrated" and layer["source_sha256"] == target.sha256
        assert layer["strength_class"] in {"weak", "medium", "strong"}
        for field in ("map", "valid_mask"):
            ref = layer[field]["artifact"]
            values = decode_npy(app.storage.blob_path("artifacts", ref["sha256"]).read_bytes())
            assert values.ndim == 2
            if field == "valid_mask":
                assert values.dtype == bool and values.any()
            else:
                assert values.dtype == np.float32 and np.isfinite(values).all()
        assert layer["detector_id"] not in result.outputs["fusion.unavailable"]
    assert "compression.dq_map" in result.outputs["fusion.unavailable"]
    assert (
        result.outputs["fusion.availability"]["outputs"]["compression.dq_map"]["state"]
        == "NOT_APPLICABLE"
    )


def test_failed_optional_detector_remains_unavailable_without_blocking_other_measurements(
    make_app, tmp_path, monkeypatch
):
    from photoproof.plugins.compression.jpeg_grid import processor

    def controlled_failure(ctx):
        raise InputRejectedError("controlled detector failure")

    monkeypatch.setattr(processor, "run", controlled_failure)
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(write_copy_image(tmp_path / "copy.png"), role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert result.status.value == "INCOMPLETE"
    assert {p.plugin_id: p.state for p in result.plugins}["fusion.normalize"] == "SUCCESS"
    assert "compression.grid_map" in result.outputs["fusion.unavailable"]
    assert not any(
        layer["detector_id"] == "compression.grid_map" for layer in result.outputs["fusion.layers"]
    )
    assert (
        result.outputs["fusion.availability"]["outputs"]["compression.grid_map"]["state"]
        == "FAILED"
    )
    assert any(layer["detector_id"] == "copymove.mask" for layer in result.outputs["fusion.layers"])

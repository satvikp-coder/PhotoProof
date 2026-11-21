"""Actual artifacts and controller scale beyond the inline JSON ceiling."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.controller import AnalysisController


def _request(app, tmp_path, *, size=(512, 768)):
    pixels = np.random.default_rng(32).integers(0, 256, (*size, 3), dtype=np.uint8)
    path = tmp_path / "synthetic-pixels.png"
    Image.fromarray(pixels).save(path)
    target = app.ingestion().ingest_path(path, role="target")[0]
    return AnalysisRequest(target=target, use_cache=False), pixels, path.read_bytes()


def test_large_layers_use_real_verified_binary_artifacts(make_app, tmp_path):
    app = make_app("mockplugins.layerprobe")
    request, pixels, original = _request(app, tmp_path)
    result = AnalysisController(app).analyze(request)
    assert result.status.value == "COMPLETED", result.plugins
    observation = result.outputs["evidence.observation"]
    assert observation["shape"] == list(pixels.shape[:2])
    assert observation["valid_count"] == pixels.shape[0] * pixels.shape[1]
    assert abs(observation["mean"] - float(np.mean(pixels[..., 0].astype(np.float32) / 255))) < 1e-7
    assert observation["source_sha256"] == request.target.sha256
    assert observation["read_only"] and observation["calibration"] == "uncalibrated"
    layers = result.outputs["evidence.layers"]
    assert len(json.dumps(layers)) < 4096  # Bulk scores/masks are not nested JSON grids.
    assert layers[0]["map"]["artifact"]["size"] > 1 << 20
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    hashes = {ref["sha256"] for ref in manifest["artifacts"]}
    assert layers[0]["map"]["artifact"]["sha256"] in hashes
    assert layers[0]["valid_mask"]["artifact"]["sha256"] in hashes
    assert app.storage.blob_path("objects", request.target.sha256).read_bytes() == original


@pytest.mark.parametrize(
    "field,value", [("source_sha256", "b" * 64), ("calibration", "calibrated:imaginary")]
)
def test_invalid_provenance_cannot_become_consumed_evidence(
    make_app, tmp_path, monkeypatch, field, value
):
    from mockplugins.layerprobe.producer import processor

    original_run = processor.run

    def wrong_source(ctx):
        outputs = original_run(ctx)
        outputs["evidence.layers"][0][field] = value
        return outputs

    monkeypatch.setattr(processor, "run", wrong_source)
    app = make_app("mockplugins.layerprobe")
    request, _, original = _request(app, tmp_path, size=(8, 12))
    result = AnalysisController(app).analyze(request)
    assert result.status.value == "INCOMPLETE"
    assert next(p.state for p in result.plugins if p.plugin_id == "evidence.consumer") == "FAILED"
    assert "evidence.observation" not in result.outputs
    assert app.storage.blob_path("objects", request.target.sha256).read_bytes() == original


def test_corrupt_binary_scores_fail_without_repair(make_app, tmp_path, monkeypatch):
    from mockplugins.layerprobe.producer import processor

    app = make_app("mockplugins.layerprobe")
    original_run = processor.run
    corrupted = []

    def corrupt(ctx):
        outputs = original_run(ctx)
        ref = outputs["evidence.layers"][0]["map"]
        path = app.storage.blob_path("artifacts", ref.sha256)
        path.chmod(0o600)
        changed = b"x" * ref.size
        path.write_bytes(changed)
        corrupted.append((path, changed))
        return outputs

    monkeypatch.setattr(processor, "run", corrupt)
    request, _, original = _request(app, tmp_path, size=(8, 12))
    result = AnalysisController(app).analyze(request)
    assert result.status.value == "INCOMPLETE"
    assert "evidence.observation" not in result.outputs
    assert all(path.read_bytes() == changed for path, changed in corrupted)
    assert app.storage.blob_path("objects", request.target.sha256).read_bytes() == original

"""Actual pixel-artifact-producer-consumer flow and cross-run denial."""

from pathlib import Path

import numpy as np
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.controller import AnalysisController


def _request(app, tmp_path):
    path = tmp_path / "pixels.png"
    Image.fromarray(np.full((8, 8, 3), 70, dtype=np.uint8)).save(path)
    target = app.ingestion().ingest_path(path, role="target")[0]
    return AnalysisRequest(target=target, use_cache=False), path.read_bytes()


def test_real_numerical_artifact_reaches_consumer(make_app, tmp_path):
    app = make_app("mockplugins.artifactprobe")
    request, original = _request(app, tmp_path)
    result = AnalysisController(app).analyze(request)
    assert result.status.value == "COMPLETED"
    assert result.outputs["artifactprobe.mean"] == 70
    assert result.outputs["artifactprobe.shape"] == [8, 8, 3]
    ref = result.outputs["artifactprobe.array"]["artifact"]
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert any(record["sha256"] == ref["sha256"] for record in manifest["artifacts"])
    assert app.storage.blob_path("objects", request.target.sha256).read_bytes() == original


def test_prior_run_reference_without_current_write_is_denied(make_app, tmp_path, monkeypatch):
    from mockplugins.artifactprobe.producer import processor

    app = make_app("mockplugins.artifactprobe")
    request, _ = _request(app, tmp_path)
    controller = AnalysisController(app)
    first = controller.analyze(request)
    reference = first.outputs["artifactprobe.array"]
    monkeypatch.setattr(
        processor,
        "run",
        lambda ctx: {
            "artifactprobe.array": reference,
            "artifactprobe.layers": first.outputs["artifactprobe.layers"],
        },
    )
    second = controller.analyze(request)
    assert second.status.value == "INCOMPLETE"
    assert {p.plugin_id: p.state for p in second.plugins}["artifactprobe.consumer"] == "FAILED"
    assert "artifactprobe.mean" not in second.outputs


def test_configured_artifact_limit_is_separate_from_ingestion(make_app, app_root, tmp_path):
    settings = app_root / "settings.yaml"
    settings.write_text(
        settings.read_text().replace(
            "max_artifact_read_bytes: 67108864", "max_artifact_read_bytes: 128"
        )
    )
    app = make_app("mockplugins.artifactprobe")
    request, original = _request(app, tmp_path)
    result = AnalysisController(app).analyze(request)
    assert result.status.value == "INCOMPLETE"
    assert {p.plugin_id: p.state for p in result.plugins} == {
        "artifactprobe.producer": "SUCCESS",
        "artifactprobe.consumer": "FAILED",
    }
    assert "artifactprobe.mean" not in result.outputs
    assert app.storage.blob_path("objects", request.target.sha256).read_bytes() == original

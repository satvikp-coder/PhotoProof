"""Heatmap consumes actual upstream map, segmentation and generated safe preview."""

import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController


def test_heatmap_controller_artifacts_and_traceability(
    make_app: Callable[..., Application], tmp_path: Path
) -> None:
    app = make_app("photoproof.plugins")
    source = tmp_path / "input.png"
    Image.fromarray(np.random.default_rng(36).integers(0, 256, (64, 96, 3), dtype=np.uint8)).save(
        source
    )
    original = source.read_bytes()
    target, _ = app.ingestion().ingest_path(source, role="target")
    controller = AnalysisController(app)
    request = AnalysisRequest(target=target, use_cache=False)
    first = controller.analyze(request)
    second = controller.analyze(request)
    states = {p.plugin_id: p.state for p in first.plugins}
    assert states["fusion.heatmap"] == "SUCCESS", first.plugins
    legend = first.outputs["fusion.legend"]
    assert legend["label"] == "heuristic suspicion score (uncalibrated)"
    assert legend["preview_sha256"] == first.outputs["support.preview"]["artifact"]["sha256"]
    manifest = app.storage.read_json("manifests", Path(first.manifest_path).name)
    for name in ("fusion.heatmap", "fusion.overlay", "fusion.outlines"):
        assert first.outputs[name] == second.outputs[name]
        ref = first.outputs[name]["artifact"]
        data = app.storage.blob_path("artifacts", ref["sha256"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == ref["sha256"]
        assert ref["sha256"] in json.dumps(manifest)
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (96, 64) and not image.info
    assert source.read_bytes() == original


def test_missing_fused_evidence_has_no_heatmap(
    make_app: Callable[..., Application], tmp_path: Path, monkeypatch
) -> None:
    from photoproof.plugins.fusion.rules import processor as rules

    monkeypatch.setattr(
        rules,
        "run",
        lambda ctx: {
            "fusion.map": None,
            "fusion.evidence": [{"detector_id": "missing", "reason": "unavailable"}],
        },
    )
    app = make_app("photoproof.plugins")
    source = tmp_path / "no-evidence.png"
    Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8)).save(source)
    target, _ = app.ingestion().ingest_path(source, role="target")
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert next(p.state for p in result.plugins if p.plugin_id == "fusion.heatmap") == "SUCCESS"
    assert result.outputs["fusion.heatmap"] is None
    assert result.outputs["fusion.overlay"] is None
    assert result.outputs["fusion.legend"]["message"] == "no fused evidence available"

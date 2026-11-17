"""Real controller persistence and upstream evidence ownership for #35."""

import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController
from photoproof.plugins.fusion.rules import processor as rules


def test_fusion_regions_runs_through_controller(
    make_app: Callable[..., Application], tmp_path: Path
) -> None:
    app = make_app("photoproof.plugins")
    pixels = np.random.default_rng(35).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    source = tmp_path / "photo.png"
    Image.fromarray(pixels).save(source)
    original = source.read_bytes()
    target, _ = app.ingestion().ingest_path(source, role="target")
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["fusion.regions"] == "SUCCESS", result.plugins
    outputs = result.outputs
    summary = outputs["fusion.segmentation"]
    assert summary["status"] in {"segmented", "no_regions"}
    ref = outputs["fusion.mask"]["artifact"]
    assert ref["producer"] == "fusion.regions"
    assert ref["media_type"] == "image/png"
    data = app.storage.blob_path("artifacts", ref["sha256"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == ref["sha256"]
    with Image.open(io.BytesIO(data)) as image:
        mask = np.array(image)
    assert mask.shape == np.array(outputs["fusion.map"]).shape
    assert set(np.unique(mask)) <= {0, 255}
    assert outputs["fusion.evidence"]  # Still the upstream producer's evidence.
    assert source.read_bytes() == original
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert ref["sha256"] in json.dumps(manifest)


def test_planted_upstream_evidence_through_controller(
    make_app: Callable[..., Application], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a deterministic fused fixture at the real upstream plugin boundary."""
    scores = np.zeros((32, 32), dtype=np.float32)
    scores[8:24, 8:24] = 0.9
    evidence = [
        {"region": {"rows": [12], "cols": [12]}, "contributing_detectors": ["copymove.mask"]}
    ]
    monkeypatch.setattr(
        rules, "run", lambda ctx: {"fusion.map": scores.tolist(), "fusion.evidence": evidence}
    )
    app = make_app("photoproof.plugins")
    source = tmp_path / "planted.png"
    Image.fromarray(np.zeros((32, 32, 3), dtype=np.uint8)).save(source)
    target, _ = app.ingestion().ingest_path(source, role="target")
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert result.outputs["fusion.evidence"] == evidence
    regions = result.outputs["fusion.regions"]
    assert len(regions) == 1
    assert regions[0]["contributing_detectors"] == ["copymove.mask"]
    ref = result.outputs["fusion.mask"]["artifact"]
    data = app.storage.blob_path("artifacts", ref["sha256"]).read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        mask = np.array(image)
    assert mask[12, 12] == 255 and mask[0, 0] == 0

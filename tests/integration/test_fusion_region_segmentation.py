"""Actual upstream pixels drive retained region masks and accepted-contribution provenance."""

import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.controller import AnalysisController
from tests.fixtures.native_images import write_copy_image


def _analyze(app, path):
    target = app.ingestion().ingest_path(path, role="target")[0]
    return AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))


def test_fusion_regions_runs_through_controller(make_app, tmp_path):
    app = make_app("photoproof.plugins")
    path = write_copy_image(tmp_path / "copy.png")
    original = path.read_bytes()
    result = _analyze(app, path)
    assert {p.plugin_id: p.state for p in result.plugins}["fusion.regions"] == "SUCCESS"
    outputs = result.outputs
    assert outputs["fusion.segmentation"]["status"] == "segmented"
    ref = outputs["fusion.mask"]["artifact"]
    assert ref["producer"] == "fusion.regions" and ref["media_type"] == "image/png"
    data = app.storage.blob_path("artifacts", ref["sha256"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == ref["sha256"]
    with Image.open(io.BytesIO(data)) as image:
        mask = np.array(image)
    assert list(mask.shape) == outputs["fusion.map"]["grid_shape"]
    assert set(np.unique(mask)) <= {0, 255}
    assert outputs["fusion.evidence"]["contributions"]
    assert path.read_bytes() == original
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert ref["sha256"] in json.dumps(manifest)


def test_planted_upstream_evidence_through_controller(make_app, tmp_path):
    app = make_app("photoproof.plugins")
    result = _analyze(app, write_copy_image(tmp_path / "copy.png"))
    regions = result.outputs["fusion.regions"]
    assert len(regions) == 2
    assert all("copymove.mask" in region["contributing_detectors"] for region in regions)
    ref = result.outputs["fusion.mask"]["artifact"]
    with Image.open(
        io.BytesIO(app.storage.blob_path("artifacts", ref["sha256"]).read_bytes())
    ) as image:
        mask = np.array(image)
    assert mask[64, 64] == mask[280, 280] == 255 and mask[0, 0] == 0


def test_actual_controller_regions_explain_accepted_copy_move(make_app, tmp_path):
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(write_copy_image(tmp_path / "explain.png"), role="target")[
        0
    ]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert {p.plugin_id: p.state for p in result.plugins}["fusion.regions"] == "SUCCESS"
    for region in result.outputs["fusion.regions"]:
        assert "geometry" in region["families"]
        assert "strong_maximum" in region["rules_fired"]
        detail = next(e for e in region["contributions"] if e["detector_id"] == "copymove.mask")
        assert detail["accepted_cell_count"] > 0
        assert detail["max_accepted_score"] > 0.7
        assert detail["measurement_scope"] == "original_hysteresis_components"
        assert 0 < detail["mean_accepted_score"] <= detail["max_accepted_score"] <= 1

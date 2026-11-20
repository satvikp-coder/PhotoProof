"""Actual detector evidence, configured weights and accepted contribution artifacts."""

import json
from pathlib import Path

import numpy as np

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npy
from photoproof.core.controller import AnalysisController
from tests.fixtures.native_images import write_copy_image


def _analyze(app, path):
    target = app.ingestion().ingest_path(path, role="target")[0]
    return AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))


def test_fusion_rules_runs_through_controller(make_app, tmp_path):
    app = make_app("photoproof.plugins")
    result = _analyze(app, write_copy_image(tmp_path / "copy.png"))
    assert {p.plugin_id: p.state for p in result.plugins}["fusion.rules"] == "SUCCESS"
    fused = result.outputs["fusion.map"]
    assert fused is not None and fused["calibration"] == "uncalibrated"
    ref = fused["scores"]["artifact"]
    values = decode_npy(app.storage.blob_path("artifacts", ref["sha256"]).read_bytes())
    assert values.ndim == 2 and values.size and np.all((values >= 0) & (values <= 1))
    evidence = result.outputs["fusion.evidence"]
    assert evidence["contributions"] and evidence["source_sha256"] == fused["source_sha256"]
    for entry in evidence["contributions"]:
        assert {
            "detector_id",
            "family",
            "strength_class",
            "scores",
            "measured",
            "effective_weight",
        } <= set(entry)
    assert "compression.dq_map" in evidence["unavailable"]
    assert "probability" not in json.dumps(result.outputs["fusion.score_info"]).lower()


def test_controller_applies_configured_fusion_weights(make_app, tmp_path):
    app = make_app("photoproof.plugins")
    (app.root / "configs" / "plugins" / "fusion.rules.yaml").write_text(
        "strength_weights:\n  strong: 0.0\n", encoding="utf-8"
    )
    result = _analyze(app, write_copy_image(tmp_path / "copy.png"))
    assert {p.plugin_id: p.state for p in result.plugins}["fusion.rules"] == "SUCCESS"
    ref = result.outputs["fusion.map"]["scores"]["artifact"]
    values = decode_npy(app.storage.blob_path("artifacts", ref["sha256"]).read_bytes())
    # Disabling copy-move must not disable independent noise measurements. Native
    # noise scores can differ across numeric builds; they are not a zero baseline.
    assert np.isfinite(values).all() and np.all((values >= 0) & (values <= 1))
    contribution = next(
        e
        for e in result.outputs["fusion.evidence"]["contributions"]
        if e["detector_id"] == "copymove.mask"
    )
    assert contribution["effective_weight"] == 0
    disabled_ref = contribution["scores"]["artifact"]
    disabled = decode_npy(app.storage.blob_path("artifacts", disabled_ref["sha256"]).read_bytes())
    assert not disabled.any()
    assert result.outputs["fusion.assessment"]["coverage"]["geometry"] == "weight_disabled"
    assert result.outputs["fusion.score_info"]["parameters"]["strength_weights"]["strong"] == 0
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert manifest["outputs"]["fusion.map"] == result.outputs["fusion.map"]

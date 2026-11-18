"""Integration tests for fusion.rules (#33) through the controller.

Proves a normal controller analysis runs fusion.rules as a required plugin
(after fusion.normalize), fusing available evidence layers into a heuristic
suspicion map and per-region evidence summaries.
"""

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest, AnalysisResult
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController
from photoproof.plugins.fusion.normalize import processor as normalize

Factory = Callable[..., Application]


def _make_png(path: Path) -> Path:
    pixels = np.zeros((16, 16, 3), dtype=np.uint8)
    pixels[..., 0] = 200
    pixels[..., 1] = 100
    Image.fromarray(pixels, "RGB").save(path)
    return path


def _analyze_ingested(app: Application, path: Path) -> AnalysisResult:
    controller = AnalysisController(app)
    target, _record = app.ingestion().ingest_path(path, role="target")
    return controller.analyze(AnalysisRequest(target=target, use_cache=False))


def test_fusion_rules_runs_through_controller(make_app: Factory, tmp_path: Path) -> None:
    """fusion.rules runs after fusion.normalize and produces fused outputs.

    The run may be INCOMPLETE (some detectors fail for format mismatch), but
    fusion.rules itself must succeed and produce fusion.map / fusion.evidence
    in the outputs.
    """
    app = make_app("photoproof.plugins")
    png = _make_png(tmp_path / "photo.png")
    result = _analyze_ingested(app, png)

    # fusion.rules must have run successfully (it requires fusion.normalize).
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states.get("fusion.rules") == "SUCCESS", f"fusion.rules state: {states}"

    outputs = result.outputs
    assert "fusion.map" in outputs
    assert "fusion.evidence" in outputs

    fmap = outputs["fusion.map"]
    if fmap is not None:
        arr = np.array(fmap, dtype=np.float32)
        assert arr.ndim == 2 and arr.size > 0
        assert np.all((arr >= 0.0) & (arr <= 1.0))  # scores in [0, 1]

    evidence = outputs["fusion.evidence"]
    assert isinstance(evidence, list)
    for entry in evidence:
        # Unavailable entries have different keys than region entries.
        if entry.get("reason") == "unavailable":
            assert "detector_id" in entry
            assert "score" in entry
        else:
            assert "contributing_detectors" in entry
            assert "families" in entry
            assert "rules_fired" in entry
            assert "max_score" in entry

    # No "probability" word anywhere in outputs.

    outputs_json = json.dumps(outputs)
    assert "probability" not in outputs_json.lower()


def test_controller_applies_configured_fusion_weights(
    make_app: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controlled upstream fixture; actual fusion config loading and persistence."""
    monkeypatch.setattr(
        normalize,
        "run",
        lambda ctx: {
            "fusion.layers": [
                {
                    "detector_id": "copymove.mask",
                    "map": [[0.9]],
                    "strength_class": "strong",
                    "valid_mask": [[True]],
                }
            ],
            "fusion.unavailable": [],
        },
    )
    app = make_app("photoproof.plugins")
    config = app.root / "configs" / "plugins" / "fusion.rules.yaml"
    config.write_text("strength_weights:\n  strong: 0.0\n", encoding="utf-8")
    result = _analyze_ingested(app, _make_png(tmp_path / "configured.png"))
    assert {p.plugin_id: p.state for p in result.plugins}["fusion.rules"] == "SUCCESS"
    assert result.outputs["fusion.map"] == [[0.0]]
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert manifest["outputs"]["fusion.map"] == [[0.0]]

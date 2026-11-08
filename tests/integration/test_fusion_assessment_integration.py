"""Integration tests for fusion.assessment (#34) through the controller.

Proves a normal controller analysis runs fusion.assessment as a required plugin
(after fusion.rules), producing an honest assessment category based on coverage.
"""

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest, AnalysisResult
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController

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


def test_fusion_assessment_runs_through_controller(make_app: Factory, tmp_path: Path) -> None:
    """fusion.assessment runs after fusion.rules and produces fusion.assessment.

    The run may be INCOMPLETE (some detectors fail for format mismatch), but
    fusion.assessment itself must succeed and produce fusion.assessment output
    with a valid category.
    """
    app = make_app("photoproof.plugins")
    png = _make_png(tmp_path / "photo.png")
    result = _analyze_ingested(app, png)

    states = {p.plugin_id: p.state for p in result.plugins}
    assert states.get("fusion.assessment") == "SUCCESS", f"fusion.assessment state: {states}"

    outputs = result.outputs
    assert "fusion.assessment" in outputs

    assessment = outputs["fusion.assessment"]
    assert "category" in assessment
    assert assessment["category"] in (
        "manipulation_indicated",
        "differences_found",
        "no_indicators_found_with_sufficient_coverage",
        "inconclusive",
    )
    assert "reasons" in assessment
    assert "coverage" in assessment
    assert "limitations" in assessment

    # No "authentic", "genuine", or "real" anywhere
    outputs_json = json.dumps(outputs)
    lower = outputs_json.lower()
    for word in ["authentic", "genuine", "real"]:
        assert word not in lower

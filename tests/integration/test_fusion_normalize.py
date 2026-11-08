"""Integration tests for fusion.normalize (#32) through the controller.

Proves a normal controller analysis runs fusion.normalize as an optional-input
plugin: it always runs (never SKIP due to absent detector outputs), normalizes
available detector outputs to EvidenceLayer objects, and marks absent detectors
as unavailable.
"""

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


def test_fusion_normalize_runs_with_available_detectors(make_app: Factory, tmp_path: Path) -> None:
    """fusion.normalize always runs and normalizes available detector outputs.

    Even when some detectors fail (e.g. format mismatch), normalize still runs
    because its inputs are optional. It normalizes available outputs to EvidenceLayer
    objects and marks absent detectors as unavailable — never as zero-score layers.
    """
    app = make_app("photoproof.plugins")
    png = _make_png(tmp_path / "photo.png")
    result = _analyze_ingested(app, png)

    # fusion.normalize must have run successfully regardless of other plugin failures.
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["fusion.normalize"] == "SUCCESS"

    outputs = result.outputs
    assert "fusion.layers" in outputs
    layers = outputs["fusion.layers"]
    assert isinstance(layers, list)
    assert len(layers) > 0  # at least some detectors produced output
    # Every layer is uncalibrated with a valid strength class.
    for layer in layers:
        assert layer["calibration"] == "uncalibrated"
        assert layer["strength_class"] in {"weak", "medium", "strong"}
        assert "map" in layer
        assert "valid_mask" in layer
    # Unavailable detectors are listed (at least some, since not all detectors
    # produce output for every input format).
    assert "fusion.unavailable" in outputs
    unavailable = outputs["fusion.unavailable"]
    assert isinstance(unavailable, list)
    assert len(unavailable) > 0

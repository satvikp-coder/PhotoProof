"""Actual controller, isolated decoder, EXIF normalization and declared geometry."""

import json
from pathlib import Path

import numpy as np
import pytest
from helpers import write_profile
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.controller import AnalysisController


def _image(app, path, orientation, role):
    pixels = np.random.default_rng(orientation).integers(0, 256, (8, 12, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif[274] = orientation
    Image.fromarray(pixels).save(path, quality=95, exif=exif)
    return app.ingestion().ingest_path(path, role=role)[0]


@pytest.mark.parametrize("orientation", range(1, 9))
def test_optional_geometry_matches_every_actual_normalized_pixel(make_app, tmp_path, orientation):
    app = make_app("mockplugins.geometryprobe")
    target = _image(app, tmp_path / "target.jpg", orientation, "target")
    controller = AnalysisController(app)
    result = controller.analyze(AnalysisRequest(target=target, use_cache=False))
    assert result.status.value == "COMPLETED"
    observation = result.outputs["geometry.observation"]
    assert observation["available"] and observation["mismatches"] == 0
    geometry = result.outputs["image.geometry"]
    assert geometry == observation["geometry"]
    assert geometry["source_sha256"] == target.sha256
    assert geometry["applied_orientation"] == orientation
    assert geometry["decoded_size"] == [12, 8]
    assert geometry["working_size"] == ([8, 12] if orientation >= 5 else [12, 8])
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert geometry == manifest["outputs"]["image.geometry"]
    assert json.loads(json.dumps(geometry)) == geometry


def test_comparison_geometry_roles_remain_distinct(make_app, app_root, tmp_path):
    # This registry exposes geometry only; its fixture profile owns no builtin requirement.
    write_profile(
        app_root, "comparison", mode="comparison", required_plugins=[], required_outputs=[]
    )
    app = make_app("mockplugins.geometryprobe")
    target = _image(app, tmp_path / "target.jpg", 6, "target")
    reference = _image(app, tmp_path / "reference.jpg", 8, "reference")
    result = AnalysisController(app).analyze(
        AnalysisRequest(target=target, reference=reference, profile="comparison", use_cache=False)
    )
    assert result.outputs["geometry.observation"]["mismatches"] == 0
    assert result.outputs["image.geometry"]["applied_orientation"] == 6
    assert result.outputs["image.reference_geometry"]["applied_orientation"] == 8
    assert result.outputs["image.reference_geometry"]["source_sha256"] == reference.sha256


def test_required_reference_geometry_without_reference_is_not_applicable(
    make_app, tmp_path, monkeypatch
):
    from mockplugins.geometryprobe.probe import METADATA

    monkeypatch.setitem(METADATA, "inputs", ("image.reference_geometry",))
    monkeypatch.setitem(METADATA, "optional_inputs", ())
    app = make_app("mockplugins.geometryprobe")
    target = _image(app, tmp_path / "single.jpg", 1, "target")
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert result.status.value == "COMPLETED"
    assert result.plugins[0].state == "NOT_APPLICABLE"
    assert "geometry.observation" not in result.outputs

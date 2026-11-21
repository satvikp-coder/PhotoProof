"""Actual isolated ingestion/registration/similarity and verified downstream consumption."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npy
from photoproof.core.config.flags import FeatureFlags
from photoproof.core.controller import AnalysisController
from photoproof.core.pipeline.discovery import discover
from photoproof.core.pipeline.registry import PluginRegistry
from tests.integration.test_reference_registration_controller import _application, _request


def _app(make_app, app_root):
    app = _application(make_app, app_root)
    app.registry = PluginRegistry(
        [*discover(), *discover("mockplugins.similarityconsumer")],
        host_api=app.identity.application.plugin_api_version,
    )
    app.flags = FeatureFlags.build(
        app.settings.features.flags,
        plugin_ids=app.registry.known_ids,
        unknown=app.settings.features.unknown_flags,
    )
    return app


@pytest.mark.parametrize("normalization", ["none", "gain_offset", "histogram"])
def test_real_similarity_artifacts_reach_manifest_and_verified_consumer(
    make_app, app_root, tmp_path, normalization
):
    # make_app reads real configuration at construction.
    (app_root / "configs" / "plugins" / "comparison.ssim.yaml").write_text(
        f"photometric_normalization: {normalization}\n", encoding="utf-8"
    )
    app = _app(make_app, app_root)
    request, paths = _request(app, tmp_path)
    original = {role: path.read_bytes() for role, path in paths.items()}
    result = AnalysisController(app).analyze(request)
    states = {p.plugin_id: p.state for p in result.plugins}
    assert (
        states["comparison.registration"]
        == states["comparison.ssim"]
        == states["comparison.similarity_probe"]
        == "SUCCESS"
    ), result.plugins
    assert result.status.value == "COMPLETED", result.plugins
    summary = result.outputs["comparison.similarity_summary"]
    assert summary["target_sha256"] == request.target.sha256
    assert summary["reference_sha256"] == request.reference.sha256
    assert summary["facts"]["normalization"]["mode"] == normalization
    assert result.outputs["comparison.similarity_probe"]["read_only"]
    assert result.outputs["comparison.similarity_probe"]["valid_cells"] > 1000
    arrays = {}
    for name, wrapper in summary["artifacts"].items():
        ref = wrapper["artifact"]
        arrays[name] = decode_npy(app.storage.blob_path("artifacts", ref["sha256"]).read_bytes())
        assert ref["producer"] == "comparison.ssim"
    valid = arrays["similarity_validity"]
    assert valid.dtype == bool and valid.shape == (192, 256)
    assert not arrays["dissimilarity_map"][~valid].any()
    assert not arrays["abs_diff_map"][~valid].any()
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert manifest["outputs"]["comparison.similarity_summary"] == summary
    for descriptor in (request.target, request.reference):
        assert (
            app.storage.blob_path("objects", descriptor.sha256).read_bytes()
            == original[descriptor.role]
        )
        assert paths[descriptor.role].read_bytes() == original[descriptor.role]


def test_failed_registration_never_becomes_zero_difference(make_app, app_root, tmp_path):
    app = _app(make_app, app_root)
    request, _ = _request(app, tmp_path, unrelated=True)
    result = AnalysisController(app).analyze(request)
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["comparison.ssim"] == "NOT_APPLICABLE", result.plugins
    assert result.outputs["comparison.transform"]["status"] == "registration_failed"
    assert "comparison.similarity_summary" not in result.outputs
    assert "comparison.dissimilarity_map" not in result.outputs


def test_actual_planted_edit_survives_registration_as_top_difference(make_app, app_root, tmp_path):
    app = _app(make_app, app_root)
    request, paths = _request(app, tmp_path)
    with Image.open(paths["target"]) as image:
        pixels = np.asarray(image).copy()
    pixels[75:110, 110:150] = 255 - pixels[75:110, 110:150]
    Image.fromarray(pixels).save(paths["target"])
    target = app.ingestion().ingest_path(paths["target"], role="target")[0]
    request = AnalysisRequest(
        target=target, reference=request.reference, profile="comparison", use_cache=False
    )
    result = AnalysisController(app).analyze(request)
    assert {p.plugin_id: p.state for p in result.plugins}["comparison.ssim"] == "SUCCESS", (
        result.plugins
    )
    row, col = result.outputs["comparison.similarity_probe"]["maximum_position"]
    assert 75 <= row < 110 and 110 <= col < 150

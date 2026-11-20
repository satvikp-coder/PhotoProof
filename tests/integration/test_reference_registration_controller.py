"""Actual immutable inputs through isolated decoding, registration and artifact consumption."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.exceptions import ConfigurationError
from photoproof.core.config.flags import FeatureFlags
from photoproof.core.controller import AnalysisController
from photoproof.core.pipeline.discovery import discover
from photoproof.core.pipeline.registry import PluginRegistry
from tests.fixtures.synthetic import textured_scene


def _application(make_app, app_root):
    disabled = [
        p.raw_metadata["id"]
        for p in discover()
        if not p.raw_metadata["id"].startswith("comparison.")
    ]
    (app_root / "configs" / "profiles" / "comparison.yaml").write_text(
        "name: comparison\ndescription: Actual registration integration fixture\n"
        "mode: comparison\nplugins:\n  enable: []\n"
        f"  disable: {json.dumps(disabled)}\n"
        "required_plugins: [comparison.registration]\nrequired_outputs: [comparison.transform]\n",
        encoding="utf-8",
    )
    app = make_app("photoproof.plugins", start=False)
    app.registry = PluginRegistry(
        [*discover(), *discover("mockplugins.registrationconsumer")],
        host_api=app.identity.application.plugin_api_version,
    )
    app.flags = FeatureFlags.build(
        app.settings.features.flags,
        plugin_ids=app.registry.known_ids,
        unknown=app.settings.features.unknown_flags,
    )
    app.storage.ensure_layout()
    app.start()
    return app


def _request(app, tmp_path, *, unrelated=False, identical=False):
    reference = textured_scene(192, 256, seed=288)
    matrix = np.array([[1, 0.02, 8], [-0.01, 1, 5], [0, 0, 1.0]])
    target = (
        textured_scene(192, 256, seed=902)
        if unrelated
        else cv2.warpPerspective(reference, matrix, (256, 192))
    )
    paths = {"target": tmp_path / "target.png", "reference": tmp_path / "reference.png"}
    Image.fromarray(reference).save(paths["reference"])
    if identical:
        paths["target"].write_bytes(paths["reference"].read_bytes())
    else:
        Image.fromarray(target).save(paths["target"])
    target_input = app.ingestion().ingest_path(paths["target"], role="target")[0]
    reference_input = app.ingestion().ingest_path(paths["reference"], role="reference")[0]
    return AnalysisRequest(
        target=target_input, reference=reference_input, profile="comparison", use_cache=False
    ), paths


def test_controller_persists_source_bound_registration_and_consumer(
    make_app, app_root, tmp_path, monkeypatch
):
    app = _application(make_app, app_root)
    request, paths = _request(app, tmp_path)
    before = {name: path.read_bytes() for name, path in paths.items()}
    controller = AnalysisController(app)
    decoder = controller._decode_bytes
    decoded_sources = []

    def count_decode(data, sha256, **kwargs):
        decoded_sources.append(sha256)
        return decoder(data, sha256, **kwargs)

    monkeypatch.setattr(controller, "_decode_bytes", count_decode)
    result = controller.analyze(request)
    assert decoded_sources == [request.target.sha256, request.reference.sha256]
    states = {p.plugin_id: p.state for p in result.plugins}
    assert (
        states["comparison.registration"] == states["comparison.registration_probe"] == "SUCCESS"
    ), result.plugins
    assert result.status.value == "COMPLETED", result.plugins
    record = result.outputs["comparison.transform"]
    assert record["status"] == "registered"
    assert record["target_sha256"] == request.target.sha256
    assert record["reference_sha256"] == request.reference.sha256
    assert record["direction"] == "reference_to_target"
    assert record["quality"]["inliers"] >= 12 and record["quality"]["reprojection_rmse"] < 1.5
    probe = result.outputs["comparison.registration_probe"]
    assert probe["read_only"] and probe["overlap_cells"] > 1000
    artifact = result.outputs["comparison.overlap_mask"]["artifact"]
    mask = decode_npy(app.storage.blob_path("artifacts", artifact["sha256"]).read_bytes())
    assert mask.dtype == bool and mask.shape == (192, 256) and not mask[:3].any()
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert manifest["outputs"]["comparison.transform"] == record
    assert (
        manifest["outputs"]["comparison.preflight"]["reference_statement"]
        == "reference: user-supplied comparison material"
    )
    for descriptor in (request.target, request.reference):
        assert (
            app.storage.blob_path("objects", descriptor.sha256).read_bytes()
            == before[descriptor.role]
        )
        assert paths[descriptor.role].read_bytes() == before[descriptor.role]


def test_unrelated_registration_is_unavailable_and_consumer_not_applicable(
    make_app, app_root, tmp_path, monkeypatch
):
    app = _application(make_app, app_root)
    request, _ = _request(app, tmp_path, unrelated=True)
    result = AnalysisController(app).analyze(request)
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["comparison.registration"] == "SUCCESS", result.plugins
    assert states["comparison.registration_probe"] == "NOT_APPLICABLE", result.plugins
    record = result.outputs["comparison.transform"]
    assert record["status"] == "registration_failed" and record["matrix"] is None
    assert record["artifacts"] is None
    assert all(
        result.outputs[name] is None
        for name in (
            "comparison.aligned_reference",
            "comparison.overlap_mask",
            "comparison.inlier_points",
        )
    )


def test_identical_inputs_keep_preflight_informational_shortcut(make_app, app_root, tmp_path):
    app = _application(make_app, app_root)
    request, _ = _request(app, tmp_path, identical=True)
    result = AnalysisController(app).analyze(request)
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["comparison.registration"] == "NOT_APPLICABLE", result.plugins
    assert result.outputs["comparison.preflight"]["message"] == "inputs are identical"
    assert "comparison.transform" not in result.outputs
    assert result.status.value == "COMPLETED"


def test_production_comparison_profile_requires_actual_registration(make_app, tmp_path):
    app = make_app("photoproof.plugins")
    assert "comparison.registration" in app.profiles["comparison"].required_plugins
    request, _ = _request(app, tmp_path)
    result = AnalysisController(app).analyze(request)
    assert {p.plugin_id: p.state for p in result.plugins}["comparison.registration"] == "SUCCESS"
    assert result.outputs["comparison.transform"]["status"] == "registered"


def test_disabled_required_registration_blocks_the_production_plan(make_app, app_root, tmp_path):
    profile = app_root / "configs" / "profiles" / "comparison.yaml"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "disable: []", "disable: [comparison.registration]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="both required and disabled"):
        make_app("photoproof.plugins")
    assert not (app_root / "ARE").exists()


def test_forbidden_required_registration_blocks_the_production_plan(make_app, app_root, tmp_path):
    settings = app_root / "settings.yaml"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            "forbidden_plugins: []", "forbidden_plugins: [comparison.registration]"
        ),
        encoding="utf-8",
    )
    app = make_app("photoproof.plugins")
    request, _ = _request(app, tmp_path)
    result = AnalysisController(app).analyze(request)
    assert result.status.value == "BLOCKED"
    assert "comparison.transform" not in result.outputs
    assert result.blocked_reasons

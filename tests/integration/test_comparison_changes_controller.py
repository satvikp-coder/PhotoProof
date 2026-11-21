"""Real registration, SSIM, changes and broker consumer; no fabricated analysis data."""

from pathlib import Path

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
from tests.integration.test_reference_registration_controller import _application, _request


def _app(make_app, app_root):
    app = _application(make_app, app_root)
    app.registry = PluginRegistry(
        [*discover(), *discover("mockplugins.changeconsumer")],
        host_api=app.identity.application.plugin_api_version,
    )
    app.flags = FeatureFlags.build(
        app.settings.features.flags,
        plugin_ids=app.registry.known_ids,
        unknown=app.settings.features.unknown_flags,
    )
    return app


def test_actual_planted_edit_reaches_typed_png_labels_regions_and_manifest(
    make_app, app_root, tmp_path, record_testsuite_property
):
    app = _app(make_app, app_root)
    request, paths = _request(app, tmp_path)
    with Image.open(paths["target"]) as image:
        target = np.asarray(image).copy()
    truth = np.zeros(target.shape[:2], bool)
    truth[65:125, 85:165] = True
    target[truth] = 255 - target[truth]
    Image.fromarray(target).save(paths["target"])
    descriptor = app.ingestion().ingest_path(paths["target"], role="target")[0]
    before = paths["target"].read_bytes()
    result = AnalysisController(app).analyze(
        AnalysisRequest(
            target=descriptor, reference=request.reference, profile="comparison", use_cache=False
        )
    )
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["comparison.change_mask"] == states["comparison.change_probe"] == "SUCCESS", (
        result.plugins
    )
    assert result.status.value == "COMPLETED", result.plugins
    record = result.outputs["comparison.change_regions"]
    assert (
        record["target_sha256"] == descriptor.sha256
        and record["reference_sha256"] == request.reference.sha256
    )
    labels_ref = record["labels"]["artifact"]
    labels = decode_npy(app.storage.blob_path("artifacts", labels_ref["sha256"]).read_bytes())
    mask = labels > 0
    iou = np.count_nonzero(mask & truth) / np.count_nonzero(mask | truth)
    record_testsuite_property("synthetic_controller_change_iou", float(iou))
    assert iou >= 0.7, iou
    png_ref = record["mask"]["artifact"]
    with Image.open(app.storage.blob_path("artifacts", png_ref["sha256"])) as image:
        np.testing.assert_array_equal(np.asarray(image) > 0, mask)
    assert all(r["category"] == "difference" for r in record["regions"])
    assert result.outputs["comparison.change_probe"]["read_only"]
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert manifest["outputs"]["comparison.change_regions"] == record
    assert paths["target"].read_bytes() == before
    assert app.storage.blob_path("objects", descriptor.sha256).read_bytes() == before


def test_actual_recompression_has_no_regions_at_default_sensitivity(make_app, app_root, tmp_path):
    app = _app(make_app, app_root)
    request, paths = _request(app, tmp_path)
    jpeg = tmp_path / "recompressed.jpg"
    with Image.open(paths["reference"]) as image:
        image.save(jpeg, quality=85)
    target = app.ingestion().ingest_path(jpeg, role="target")[0]
    result = AnalysisController(app).analyze(
        AnalysisRequest(
            target=target, reference=request.reference, profile="comparison", use_cache=False
        )
    )
    assert {p.plugin_id: p.state for p in result.plugins}["comparison.change_probe"] == "SUCCESS", (
        result.plugins
    )
    assert (
        result.outputs["comparison.change_regions"]["facts"]["status"] == "no_differences_detected"
    )
    assert result.outputs["comparison.change_regions"]["regions"] == []


def test_production_comparison_requires_real_change_outputs(make_app, tmp_path):
    app = make_app("photoproof.plugins")
    profile = app.profiles["comparison"]
    assert "comparison.change_mask" in profile.required_plugins
    assert {"comparison.change_mask", "comparison.change_regions"} <= set(profile.required_outputs)
    request, _ = _request(app, tmp_path)
    result = AnalysisController(app).analyze(request)
    assert {p.plugin_id: p.state for p in result.plugins}["comparison.change_mask"] == "SUCCESS", (
        result.plugins
    )
    assert result.outputs["comparison.change_regions"]["facts"]["category"] == "difference"


def test_failed_registration_cannot_create_empty_clean_change_mask(make_app, app_root, tmp_path):
    app = _app(make_app, app_root)
    request, _ = _request(app, tmp_path, unrelated=True)
    result = AnalysisController(app).analyze(request)
    assert result.outputs["comparison.transform"]["status"] == "registration_failed"
    assert "comparison.change_mask" not in result.outputs
    assert "comparison.change_regions" not in result.outputs


def test_disabled_required_change_plugin_is_rejected_before_runtime(make_app, app_root):
    profile = app_root / "configs" / "profiles" / "comparison.yaml"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "disable: []", "disable: [comparison.change_mask]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="both required and disabled"):
        make_app("photoproof.plugins")
    assert not (app_root / "ARE").exists()


def test_forbidden_required_change_plugin_blocks_production_comparison(
    make_app, app_root, tmp_path
):
    settings = app_root / "settings.yaml"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            "forbidden_plugins: []", "forbidden_plugins: [comparison.change_mask]"
        ),
        encoding="utf-8",
    )
    app = make_app("photoproof.plugins")
    request, _ = _request(app, tmp_path)
    result = AnalysisController(app).analyze(request)
    assert result.status.value == "BLOCKED"
    assert "comparison.change_regions" not in result.outputs

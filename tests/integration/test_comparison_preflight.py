"""Reference evidence, once-only decoding, relationship checks and shared budgets."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from photoproof.cli import main as cli_main
from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.controller import AnalysisController


def _pair(app, tmp_path, *, identical=False, size=(32, 32), reference_size=None):
    target = tmp_path / "target.png"
    reference = tmp_path / "reference.png"
    Image.fromarray(np.full((*size[::-1], 3), 20, dtype=np.uint8)).save(target)
    if identical:
        reference.write_bytes(target.read_bytes())
    else:
        refsize = reference_size or size
        Image.fromarray(np.full((*refsize[::-1], 3), 180, dtype=np.uint8)).save(reference)
    t, _ = app.ingestion().ingest_path(target, role="target")
    r, _ = app.ingestion().ingest_path(reference, role="reference")
    return AnalysisRequest(target=t, reference=r, profile="comparison", use_cache=False)


def test_reference_is_distinct_and_decoded_once(make_app, tmp_path, monkeypatch):
    app = make_app("mockplugins.comparisonprobe")
    request = _pair(app, tmp_path)
    controller = AnalysisController(app)
    decode = controller._decode_bytes
    calls = []

    def counted(data, sha, **kwargs):
        calls.append(sha)
        return decode(data, sha, **kwargs)

    monkeypatch.setattr(controller, "_decode_bytes", counted)
    result = controller.analyze(request)
    assert result.status.value == "COMPLETED"
    assert calls == [request.target.sha256, request.reference.sha256]
    observation = result.outputs["comparison.observation"]
    assert observation["target_mean"] == 20 and observation["reference_mean"] == 180
    assert observation["read_only"]
    preflight = result.outputs["comparison.preflight"]
    assert not preflight["identical_bytes"] and not preflight["identical_pixels"]
    assert preflight["reference_statement"] == "reference: user-supplied comparison material"
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert manifest["outputs"]["comparison.preflight"] == preflight
    for descriptor in [request.target, request.reference]:
        assert (
            app.storage.blob_path("objects", descriptor.sha256).read_bytes()
            == (tmp_path / (descriptor.role + ".png")).read_bytes()
        )


@pytest.mark.parametrize("required", [False, True])
def test_identical_bytes_complete_without_comparison_execution(
    make_app, app_root, tmp_path, monkeypatch, required
):
    if required:
        profile = app_root / "configs" / "profiles" / "comparison.yaml"
        profile.write_text(
            profile.read_text().replace(
                "required_plugins: []", "required_plugins: [comparison.probe]"
            )
        )
    app = make_app("mockplugins.comparisonprobe")
    request = _pair(app, tmp_path, identical=True)
    controller = AnalysisController(app)
    decode = controller._decode_bytes
    calls = []

    def counted(data, sha, **kwargs):
        calls.append(sha)
        return decode(data, sha, **kwargs)

    monkeypatch.setattr(controller, "_decode_bytes", counted)
    result = controller.analyze(request)
    assert result.status.value == "COMPLETED"
    assert len(calls) == 1  # Immutable duplicate pixels are shared, never decoded twice.
    assert result.plugins[0].state == "NOT_APPLICABLE"
    assert result.outputs["comparison.preflight"]["message"] == "inputs are identical"
    assert "comparison.observation" not in result.outputs


def test_combined_pixel_budget_rejects_reference_before_allocation(
    make_app, app_root, tmp_path, monkeypatch
):
    settings = app_root / "settings.yaml"
    text = settings.read_text().replace("max_image_pixels: 178956970", "max_image_pixels: 6000")
    settings.write_text(text)
    app = make_app("mockplugins.comparisonprobe")
    controller = AnalysisController(app)
    decode = controller._decode_bytes
    budgets = []

    def counted(data, sha, **kwargs):
        budgets.append(kwargs["limits"].max_pixels)
        return decode(data, sha, **kwargs)

    monkeypatch.setattr(controller, "_decode_bytes", counted)
    result = controller.analyze(_pair(app, tmp_path, size=(64, 64)))
    assert result.status.value == "INCOMPLETE"
    assert result.outputs["comparison.preflight"]["status"] == "unavailable"
    assert budgets == [6000, 1904]


def test_reencoded_identical_pixels_are_recorded(make_app, tmp_path):
    app = make_app("mockplugins.comparisonprobe")
    pixels = np.random.default_rng(27).integers(0, 256, (32, 32, 3), dtype=np.uint8)
    paths = [tmp_path / "target.png", tmp_path / "reference.png"]
    descriptors = []
    for path, role, compression in zip(paths, ["target", "reference"], [0, 9], strict=True):
        Image.fromarray(pixels).save(path, compress_level=compression)
        descriptors.append(app.ingestion().ingest_path(path, role=role)[0])
    result = AnalysisController(app).analyze(
        AnalysisRequest(
            target=descriptors[0],
            reference=descriptors[1],
            profile="comparison",
            use_cache=False,
        )
    )
    info = result.outputs["comparison.preflight"]
    assert not info["identical_bytes"] and info["identical_pixels"]
    assert result.plugins[0].state == "SUCCESS"


def test_resized_pair_warns_without_rejecting(make_app, tmp_path):
    app = make_app("mockplugins.comparisonprobe")
    result = AnalysisController(app).analyze(
        _pair(app, tmp_path, size=(64, 32), reference_size=(16, 8))
    )
    info = result.outputs["comparison.preflight"]
    assert result.status.value == "COMPLETED"
    assert info["dimension_ratio"] == {"width": 4.0, "height": 4.0, "area": 16.0}
    assert info["aspect_ratio_delta"] == 0.0 and info["warnings"]


def test_bad_reference_does_not_become_zero_pixels(make_app, tmp_path):
    app = make_app("mockplugins.comparisonprobe")
    request = _pair(app, tmp_path)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xe0truncated-reference")
    reference = app.ingestion().ingest_path(bad, role="reference")[0]
    result = AnalysisController(app).analyze(request.model_copy(update={"reference": reference}))
    assert result.status.value == "INCOMPLETE"
    assert result.outputs["comparison.preflight"]["status"] == "unavailable"
    assert "comparison.observation" not in result.outputs


def test_cli_ingests_both_inputs_and_persists_preflight(app_root, tmp_path, capsys):
    target = tmp_path / "cli-target.png"
    reference = tmp_path / "cli-reference.png"
    Image.fromarray(np.full((32, 32, 3), 40, dtype=np.uint8)).save(target)
    Image.fromarray(np.full((32, 32, 3), 90, dtype=np.uint8)).save(reference)
    code = cli_main(
        [
            "analyze",
            str(target),
            "--reference",
            str(reference),
            "--profile",
            "comparison",
            "--json",
        ],
        root=app_root,
    )
    result = json.loads(capsys.readouterr().out)
    # Other detector integration can still be incomplete; this test makes no accuracy claim.
    assert result["status"] in {"COMPLETED", "INCOMPLETE"}
    assert code == (0 if result["status"] == "COMPLETED" else 7)
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert {entry["role"] for entry in manifest["inputs"]} == {"target", "reference"}
    info = manifest["outputs"]["comparison.preflight"]
    assert info["status"] == "ready"
    assert info["reference_statement"] == "reference: user-supplied comparison material"


@pytest.mark.parametrize("field,value", [("size", 1), ("media_format", "jpeg")])
@pytest.mark.parametrize("identical", [False, True])
def test_reference_descriptor_must_match_verified_evidence(
    make_app, tmp_path, field, value, identical
):
    app = make_app("mockplugins.comparisonprobe")
    request = _pair(app, tmp_path, identical=identical)
    reference = request.reference.model_copy(update={field: value})
    result = AnalysisController(app).analyze(request.model_copy(update={"reference": reference}))
    assert result.status.value == "INCOMPLETE"
    assert result.outputs["comparison.preflight"]["status"] == "unavailable"
    assert "comparison.observation" not in result.outputs


def test_target_and_reference_may_use_different_formats(make_app, tmp_path):
    app = make_app("mockplugins.comparisonprobe")
    request = _pair(app, tmp_path)
    path = tmp_path / "reference.jpg"
    Image.fromarray(np.full((32, 32, 3), 180, dtype=np.uint8)).save(path, format="JPEG")
    reference = app.ingestion().ingest_path(path, role="reference")[0]
    result = AnalysisController(app).analyze(request.model_copy(update={"reference": reference}))
    assert result.status.value == "COMPLETED"
    assert result.outputs["comparison.preflight"]["status"] == "ready"
    assert result.outputs["comparison.observation"]["read_only"]


def test_unspecified_reference_format_is_resolved_from_evidence(make_app, tmp_path):
    app = make_app("mockplugins.comparisonprobe")
    request = _pair(app, tmp_path)
    reference = request.reference.model_copy(update={"media_format": None})
    result = AnalysisController(app).analyze(request.model_copy(update={"reference": reference}))
    assert result.status.value == "COMPLETED"
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert (
        next(row for row in manifest["inputs"] if row["role"] == "reference")["media_format"]
        == "png"
    )

"""Status snapshots preserve failure/applicability without manufacturing measurements."""

from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.controller import AnalysisController


def test_consumer_sees_actual_upstream_states_and_missing_values_stay_missing(
    make_app, tmp_path, monkeypatch
):
    from mockplugins.statusprobe.available import METADATA

    monkeypatch.setitem(METADATA, "required", True)
    app = make_app("mockplugins.statusprobe")
    path = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "gray").save(path)
    original = path.read_bytes()
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert result.status.value == "INCOMPLETE"
    assert result.outputs["probe.snapshot"]["required_prior_outputs"] == ["probe.available"]
    states = result.outputs["probe.snapshot"]["outputs"]
    assert states["probe.available"]["state"] == "SUCCESS"
    assert states["probe.failed"]["state"] == "FAILED"
    assert states["probe.inapplicable"]["state"] == "NOT_APPLICABLE"
    assert states["probe.late"]["state"] == "PENDING"
    assert states["probe.failed"]["failure_category"] is not None
    assert "probe.snapshot" not in states  # A plugin cannot describe its own future output.
    assert "probe.failed" not in result.outputs and "probe.inapplicable" not in result.outputs
    assert all(
        set(value) == {"plugin_id", "state", "failure_category"} for value in states.values()
    )
    final_snapshot = result.outputs["probe.late"]["outputs"]
    assert final_snapshot["probe.snapshot"]["state"] == "SUCCESS"
    assert states["probe.late"]["state"] == "PENDING"  # Earlier snapshots remain unchanged.
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original


def test_optional_status_capability_is_provided_without_missing_optional_values(
    make_app, tmp_path, monkeypatch
):
    from mockplugins.statusprobe.consumer import METADATA

    monkeypatch.setitem(METADATA, "inputs", ())
    monkeypatch.setitem(
        METADATA,
        "optional_inputs",
        ("pipeline.upstream_status", "probe.available", "probe.failed", "probe.inapplicable"),
    )
    app = make_app("mockplugins.statusprobe")
    path = tmp_path / "optional.png"
    Image.new("RGB", (8, 8), "gray").save(path)
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert next(p.state for p in result.plugins if p.plugin_id == "status.consumer") == "SUCCESS"
    assert (
        result.outputs["probe.snapshot"]["outputs"]["probe.inapplicable"]["state"]
        == "NOT_APPLICABLE"
    )
    assert "probe.inapplicable" not in result.outputs

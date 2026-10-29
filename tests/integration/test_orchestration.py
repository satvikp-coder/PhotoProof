"""Full workflow: bootstrap → policy → plan → execute → manifest → persistence."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import plugin_config, update_settings, write_profile

from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.contracts.enums import RunStatus
from photoproof.contracts.events import EventType
from photoproof.contracts.exceptions import InputRejectedError, PolicyError
from photoproof.core.bootstrap import Application
from photoproof.core.config.policy import BlacklistOverride, OverrideOrigin
from photoproof.core.controller import AnalysisController

TARGET = InputDescriptor(role="target", sha256="a" * 64, size=10)
REFERENCE = InputDescriptor(role="reference", sha256="b" * 64, size=10)
Factory = Callable[..., Application]


def _analyze(app: Application, **fields: object) -> object:
    override = fields.pop("override", None)
    request = AnalysisRequest(target=TARGET, **fields)  # type: ignore[arg-type]
    return AnalysisController(app).analyze(request, override=override)  # type: ignore[arg-type]


def test_complete_run_with_mock_plugins(make_app: Factory) -> None:
    app = make_app()
    seen: list[EventType] = []
    app.bus.subscribe(lambda e: seen.append(e.type))
    result = _analyze(app)
    assert result.status is RunStatus.COMPLETED  # type: ignore[attr-defined]
    states = {p.plugin_id: p.state for p in result.plugins}  # type: ignore[attr-defined]
    assert states == {
        "demo.source": "SUCCESS",
        "demo.consumer": "SUCCESS",
        "demo.configurable": "SUCCESS",
        "demo.comparison": "NOT_APPLICABLE",
        "demo.observer": "SUCCESS",
    }
    for expected in (
        EventType.RUN_CREATED,
        EventType.RUN_STARTED,
        EventType.PLUGIN_STARTED,
        EventType.ARTIFACT_CREATED,
        EventType.CACHE_MISS,
        EventType.RUN_COMPLETED,
    ):
        assert expected in seen

    manifest = json.loads(Path(result.manifest_path).read_text())  # type: ignore[attr-defined]
    assert manifest["execution_order"] == [  # priority, then discovery (package-name) order
        "demo.source",
        "demo.comparison",
        "demo.configurable",
        "demo.consumer",
        "demo.observer",
    ]
    assert manifest["inputs"][0]["sha256"] == TARGET.sha256
    assert manifest["plugins"]["demo.source"]["python_dependencies"]["pydantic"]
    assert manifest["dependency_graph"]["demo.consumer"]["requires"] == ["demo.source"]
    assert len(manifest["artifacts"]) == 1
    assert app.storage.verify_blob("artifacts", manifest["artifacts"][0]["sha256"])

    db = app.database()
    run = db.get_run(result.run_id)  # type: ignore[attr-defined]
    assert run and run["status"] == "COMPLETED" and run["manifest_sha256"]
    assert db.plugin_states(result.run_id)["demo.observer"] == "SUCCESS"  # type: ignore[attr-defined]


def test_no_detectors_installed(make_app: Factory) -> None:
    app = make_app("photoproof.plugins")
    result = _analyze(app)
    assert result.status is RunStatus.NO_DETECTORS and result.plugins == ()  # type: ignore[attr-defined]
    assert "verdict" not in result.model_dump()  # type: ignore[attr-defined]


def test_deterministic_manifests(make_app: Factory) -> None:
    app = make_app()
    first = _analyze(app, use_cache=False)
    second = _analyze(app, use_cache=False)
    load = lambda r: json.loads(Path(r.manifest_path).read_text())  # noqa: E731
    assert load(first)["computational_fingerprint"] == load(second)["computational_fingerprint"]
    assert first.run_id != second.run_id  # type: ignore[attr-defined]


def test_comparison_profile(make_app: Factory) -> None:
    app = make_app()
    result = _analyze(app, profile="comparison", reference=REFERENCE)
    assert {p.plugin_id: p.state for p in result.plugins}["demo.comparison"] == "SUCCESS"  # type: ignore[attr-defined]
    assert result.outputs["demo.compared"] is False  # type: ignore[attr-defined]
    with pytest.raises(InputRejectedError, match="requires a reference"):
        _analyze(app, profile="comparison")
    with pytest.raises(InputRejectedError, match="does not accept"):
        _analyze(app, reference=REFERENCE)


def test_blacklisted_plugin_never_runs(make_app: Factory, app_root: Path) -> None:
    update_settings(
        app_root,
        plugins={"blacklist": ["demo.source"]},
        features={"flags": {"plugin.demo.source": True}},
    )
    result = _analyze(make_app())
    states = {p.plugin_id: p.state for p in result.plugins}  # type: ignore[attr-defined]
    assert "demo.source" not in states and states["demo.consumer"] == "SKIPPED"
    assert result.status is RunStatus.INCOMPLETE  # type: ignore[attr-defined]


def test_authorized_bypass_is_audited(make_app: Factory, app_root: Path) -> None:
    update_settings(app_root, plugins={"blacklist": ["demo.source"]})
    app = make_app()
    result = _analyze(app, override=BlacklistOverride(OverrideOrigin.LOCAL_CLI))
    assert {p.plugin_id: p.state for p in result.plugins}["demo.source"] == "SUCCESS"  # type: ignore[attr-defined]
    manifest = json.loads(Path(result.manifest_path).read_text())  # type: ignore[attr-defined]
    assert manifest["blacklist_override"] is True
    assert manifest["policy"]["demo.source"]["source"] == "blacklist_bypassed"
    audit = (app.storage.root / "logs" / "audit.jsonl").read_text()
    assert "POLICY_OVERRIDE" in audit and "demo.source" in audit


def test_web_origin_cannot_bypass(make_app: Factory, app_root: Path) -> None:
    update_settings(app_root, plugins={"blacklist": ["demo.source"]})
    with pytest.raises(PolicyError):
        _analyze(make_app(), override=BlacklistOverride(OverrideOrigin.WEB))


def test_required_plugin_blacklisted_blocks_run(make_app: Factory, app_root: Path) -> None:
    update_settings(app_root, plugins={"blacklist": ["demo.source"]})
    write_profile(app_root, "strict", required_plugins=["demo.source"])
    result = _analyze(make_app(), profile="strict")
    assert result.status is RunStatus.BLOCKED and result.plugins == ()  # type: ignore[attr-defined]
    assert "blacklist" in result.blocked_reasons[0]  # type: ignore[attr-defined]


def test_required_plugin_failure_aborts(make_app: Factory, app_root: Path) -> None:
    plugin_config(app_root, "demo.configurable", behavior="raise")
    write_profile(app_root, "strict", required_plugins=["demo.configurable"])
    result = _analyze(make_app(), profile="strict")
    states = {p.plugin_id: p.state for p in result.plugins}  # type: ignore[attr-defined]
    assert result.status is RunStatus.ABORTED  # type: ignore[attr-defined]
    assert states["demo.configurable"] == "FAILED" and states["demo.observer"] == "ABORTED"


def test_invalid_plugin_config_fails_only_that_plugin(make_app: Factory, app_root: Path) -> None:
    plugin_config(app_root, "demo.configurable", behavior="explode")
    result = _analyze(make_app())
    by_id = {p.plugin_id: p for p in result.plugins}  # type: ignore[attr-defined]
    assert by_id["demo.configurable"].failure_category == "config_error"
    assert by_id["demo.source"].state == "SUCCESS"


def test_profile_disables_plugin(make_app: Factory, app_root: Path) -> None:
    write_profile(app_root, "lean", plugins={"disable": ["demo.configurable"]})
    result = _analyze(make_app(), profile="lean")
    assert "demo.configurable" not in {p.plugin_id for p in result.plugins}  # type: ignore[attr-defined]

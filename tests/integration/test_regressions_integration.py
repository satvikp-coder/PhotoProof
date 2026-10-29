"""End-to-end regression tests for defects found in the code review."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import RESULTS, meta, registry_of, update_settings

from photoproof.cli import main as cli_main
from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.contracts.enums import RunStatus, Severity
from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import EventDeliveryError
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController
from photoproof.core.health.checks import default_registry

Factory = Callable[..., Application]
TARGET = InputDescriptor(role="target", sha256="a" * 64, size=1)


def _run(app: Application, **fields: object):  # type: ignore[no-untyped-def]
    return AnalysisController(app).analyze(AnalysisRequest(target=TARGET, **fields))  # type: ignore[arg-type]


def test_plugins_cannot_mutate_each_others_outputs(make_app: Factory, tmp_path: Path) -> None:
    app = make_app()
    app.registry = registry_of(
        tmp_path,
        meta("p.a", priority=0),
        meta("p.b", requires=["p.a"], inputs=["p.a.out"]),
    )
    RESULTS["p.a"] = {"p.a.out": {"x": 1}}

    def mutate(ctx):  # type: ignore[no-untyped-def]
        ctx.inputs["p.a.out"]["x"] = 99
        return {"p.b.out": 1}

    RESULTS["p.b"] = mutate
    from photoproof.core.config.flags import FeatureFlags

    app.flags = FeatureFlags.build({}, plugin_ids=app.registry.known_ids, unknown="error")
    try:
        result = _run(app, use_cache=False)
    finally:
        RESULTS.clear()
    assert result.status is RunStatus.COMPLETED
    assert result.outputs["p.a.out"] == {"x": 1}


def test_malformed_plugin_yaml_fails_only_that_plugin_with_cache(
    make_app: Factory, app_root: Path
) -> None:
    (app_root / "configs" / "plugins" / "demo.configurable.yaml").write_text("behavior: [")
    result = _run(make_app())  # cache enabled
    by_id = {p.plugin_id: p for p in result.plugins}
    assert by_id["demo.configurable"].failure_category == "config_error"
    assert by_id["demo.source"].state == "SUCCESS"


def test_unexpected_failure_leaves_no_unfinished_run(make_app: Factory) -> None:
    app = make_app()

    def failing(event: Event) -> None:
        raise OSError("disk full")

    app.bus.subscribe(failing, EventType.PLUGIN_COMPLETED, critical=True, name="flaky")
    with pytest.raises(EventDeliveryError):
        _run(app, use_cache=False)
    runs = app.database().recent_runs()
    assert runs[0]["status"] == "ABORTED" and runs[0]["finished_at"]


def test_cache_hit_is_persisted(make_app: Factory) -> None:
    app = make_app()
    _run(app)
    hit = _run(app)
    assert hit.cache_hit
    db = app.database()
    assert db.plugin_states(hit.run_id)["demo.source"] == "SUCCESS"
    rows = db._conn.execute("SELECT COUNT(*) FROM artifacts WHERE run_id = ?", (hit.run_id,))
    assert rows.fetchone()[0] == 1


def test_duplicate_artifact_names_fail_the_plugin(make_app: Factory, tmp_path: Path) -> None:
    app = make_app()
    app.registry = registry_of(tmp_path, meta("p.a"))

    def twice(ctx):  # type: ignore[no-untyped-def]
        ref = ctx.write_artifact("same.bin", b"1", "application/octet-stream")
        ctx.write_artifact("same.bin", b"2", "application/octet-stream")
        return {"p.a.out": ref}

    RESULTS["p.a"] = twice
    from photoproof.core.config.flags import FeatureFlags

    app.flags = FeatureFlags.build({}, plugin_ids=app.registry.known_ids, unknown="error")
    try:
        result = _run(app, use_cache=False)
    finally:
        RESULTS.clear()
    assert result.plugins[0].state == "FAILED" and "already written" in str(
        result.plugins[0].message
    )


def test_quick_checks_skip_hashing_deep_checks_do_not(make_app: Factory) -> None:
    app = make_app()
    sha = app.storage.put_blob("artifacts", b"payload")
    path = app.storage.blob_path("artifacts", sha)
    path.chmod(0o600)
    path.write_bytes(b"tampered")
    quick = {r.check_id: r for r in default_registry(deep=False).run_all(app)}
    deep = {r.check_id: r for r in default_registry(deep=True).run_all(app)}
    assert quick["storage.integrity"].ok
    assert deep["storage.integrity"].severity is Severity.ERROR


def test_unknown_blacklist_entry_is_reported_and_persisted(
    app_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    update_settings(app_root, plugins={"blacklist": ["typo.plugin"]})
    assert cli_main(["status"], root=app_root) == 0
    assert "plugins.policy" in capsys.readouterr().err
    from photoproof.core.database.store import Database

    db = Database.open(app_root / "ARE" / "database" / "photoproof.db")
    checks = [r[0] for r in db._conn.execute("SELECT check_id FROM health_checks")]
    db.close()
    assert "plugins.policy" in checks


def test_stale_runs_detected_and_repaired(make_app: Factory) -> None:
    app = make_app()
    db = app.database()
    db.create_run("stale", mode="single", profile="standard", cache_key=None)
    db._conn.execute("UPDATE runs SET created_at = '2000-01-01T00:00:00+00:00'")
    result = {r.check_id: r for r in default_registry().run_all(app)}["database.stale_runs"]
    assert result.severity is Severity.WARNING
    assert result.repair and result.repair.apply
    result.repair.apply()
    assert db.get_run("stale")["status"] == "ABORTED"  # type: ignore[index]


def test_tampered_manifest_is_refused(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["analyze", str(target_file), "--json"], root=app_root) == 0
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    path = app_root / "ARE" / "manifests" / f"{run_id}.json"
    data = json.loads(path.read_text())
    data["status"] = "COMPLETED"
    path.write_text(json.dumps(data))
    assert cli_main(["manifest", run_id], root=app_root) == 1
    assert "does not match" in capsys.readouterr().err


def test_plugins_unknown_profile_is_a_config_error(
    app_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["plugins", "--profile", "nope"], root=app_root) == 4
    assert "unknown profile" in capsys.readouterr().err

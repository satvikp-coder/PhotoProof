"""Regression tests for defects found in the code review (one test per defect)."""

import sqlite3
from pathlib import Path

import pytest
from helpers import meta, registry_of, update_settings, write_profile

from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.contracts.events import EventType
from photoproof.contracts.exceptions import (
    ConfigurationError,
    DatabaseError,
    MigrationRequiredError,
)
from photoproof.core.config import loader
from photoproof.core.config.loader import load_identity, load_profiles, load_settings
from photoproof.core.config.policy import DecisionSource, PolicyDecision
from photoproof.core.config.validator import Profile
from photoproof.core.database import store
from photoproof.core.database.store import LATEST_VERSION, Database
from photoproof.core.events.bus import EventBus
from photoproof.core.pipeline.discovery import implementation_digest
from photoproof.core.pipeline.executor import (
    MAX_OUTPUT_BYTES,
    InvalidOutputError,
    normalize_outputs,
)
from photoproof.core.pipeline.planner import plan


def _settings(root: Path):  # type: ignore[no-untyped-def]
    return load_settings(root, load_identity(root), environ={})


def test_strict_booleans_reject_coercion(app_root: Path) -> None:
    update_settings(app_root, security={"allow_blacklist_bypass": "yes"})
    with pytest.raises(ConfigurationError, match="allow_blacklist_bypass"):
        _settings(app_root)
    update_settings(
        app_root,
        security={"allow_blacklist_bypass": True},
        features={"flags": {"cache.run_level": 1}},
    )
    with pytest.raises(ConfigurationError, match=r"cache\.run_level"):
        _settings(app_root)


def test_policy_ids_must_be_well_formed(app_root: Path) -> None:
    update_settings(app_root, plugins={"blacklist": ["Not An Id"]})
    with pytest.raises(ConfigurationError, match="invalid plugin identifiers"):
        _settings(app_root)


def test_profile_cannot_require_and_disable(app_root: Path) -> None:
    write_profile(app_root, "bad", required_plugins=["a.b"], plugins={"disable": ["a.b"]})
    with pytest.raises(ConfigurationError, match="both required and disabled"):
        load_profiles(app_root)


def test_oversized_config_rejected(app_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "MAX_CONFIG_BYTES", 10)
    with pytest.raises(ConfigurationError, match="larger than"):
        _settings(app_root)


def test_required_plugins_imply_enable(app_root: Path, tmp_path: Path) -> None:
    from photoproof.contracts.plugins import PluginMetadata
    from photoproof.core.config.flags import FeatureFlags
    from photoproof.core.config.policy import decide

    plugin = PluginMetadata.model_validate(meta("x.plugin", enabled_default=False))
    profile = Profile(name="t", description="t", mode="single", required_plugins=("x.plugin",))
    flags = FeatureFlags.build({}, plugin_ids={"x.plugin"}, unknown="error")
    decision = decide(
        plugin, settings=_settings(app_root), flags=flags, profile=profile, override=None
    )
    assert decision.enabled and decision.source is DecisionSource.PROFILE


def test_required_reference_plugin_blocks_single_profile(app_root: Path, tmp_path: Path) -> None:
    registry = registry_of(tmp_path, meta("p.cmp", inputs=["input.reference"], required=True))
    decisions = {"p.cmp": PolicyDecision("p.cmp", True, DecisionSource.DEFAULT, "")}
    result = plan(
        registry,
        decisions,
        settings=_settings(app_root),
        profile=Profile(name="t", description="t", mode="single"),
    )
    assert result.blocked and "needs a reference image" in result.blocked_reasons[0]


def test_output_size_cap(tmp_path: Path) -> None:
    plugin = next(iter(registry_of(tmp_path, meta("p.a"))))
    with pytest.raises(InvalidOutputError, match=r"ctx\.write_artifact"):
        normalize_outputs(plugin, {"p.a.out": "x" * (MAX_OUTPUT_BYTES + 1)})


def test_implementation_digest_covers_data_and_subpackages(tmp_path: Path) -> None:
    (tmp_path / "processor.py").write_text("x = 1\n")
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "q.json").write_text("[1]")
    base = implementation_digest(tmp_path)
    (tmp_path / "tables" / "q.json").write_text("[2]")
    assert implementation_digest(tmp_path) != base
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\0")
    after = implementation_digest(tmp_path)
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\1")
    assert implementation_digest(tmp_path) == after  # bytecode does not count


def test_event_payloads_are_read_only() -> None:
    bus, seen = EventBus(), []

    def vandal(event):  # type: ignore[no-untyped-def]
        event.data["state"] = "SUCCESS"

    bus.subscribe(vandal)
    bus.subscribe(lambda e: seen.append(dict(e.data)))
    bus.emit(EventType.PLUGIN_FAILED, data={"state": "FAILED"})
    assert seen == [{"state": "FAILED"}]


def test_request_roles_enforced() -> None:
    reference = InputDescriptor(role="reference", sha256="a" * 64, size=1)
    with pytest.raises(ValueError, match="role 'target'"):
        AnalysisRequest(target=reference)


def test_non_sqlite_file_is_a_database_error(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(DatabaseError, match="unreadable"):
        Database.open(path)


def test_migration_required_has_its_own_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.db"
    Database.open(path).close()
    monkeypatch.setattr(store, "MIGRATIONS", (*store.MIGRATIONS, "CREATE TABLE extra (x)"))
    monkeypatch.setattr(store, "LATEST_VERSION", LATEST_VERSION + 1)
    with pytest.raises(MigrationRequiredError):
        Database.open(path)


def test_unfinished_runs_can_be_marked_aborted(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "x.db")
    db.create_run("r1", mode="single", profile="p", cache_key=None)
    db.set_run_status("r1", "RUNNING")
    assert [r["run_id"] for r in db.unfinished_runs(older_than="9999")] == ["r1"]
    db.mark_runs_aborted(["r1"])
    run = db.get_run("r1")
    assert run and run["status"] == "ABORTED" and run["finished_at"]
    db.close()
    sqlite3.connect(tmp_path / "x.db").close()

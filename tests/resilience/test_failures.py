"""Fault injection against the real orchestration path."""

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import plugin_config

from photoproof.cli import main as cli_main
from photoproof.contracts.analysis import AnalysisRequest, AnalysisResult, InputDescriptor
from photoproof.contracts.enums import RunStatus
from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import EventDeliveryError
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController
from photoproof.core.health.checks import default_registry
from photoproof.core.health.repair import preflight

Factory = Callable[..., Application]


def _run(app: Application) -> AnalysisResult:
    target = InputDescriptor(role="target", sha256="a" * 64, size=1)
    return AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))


def _state(result: AnalysisResult, pid: str) -> tuple[str, str | None]:
    plugin = next(p for p in result.plugins if p.plugin_id == pid)
    return plugin.state, plugin.failure_category


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [
        ("raise", ("FAILED", "exception")),
        ("invalid_output", ("FAILED", "invalid_output")),
        ("not_json", ("FAILED", "invalid_output")),
        ("sleep", ("TIMEOUT", "timeout")),
        ("not_applicable", ("NOT_APPLICABLE", None)),
    ],
)
def test_optional_plugin_faults_are_isolated(
    make_app: Factory, app_root: Path, behavior: str, expected: tuple[str, str | None]
) -> None:
    plugin_config(app_root, "demo.configurable", behavior=behavior)
    result = _run(make_app())
    assert _state(result, "demo.configurable") == expected
    assert _state(result, "demo.consumer") == ("SUCCESS", None)
    assert _state(result, "demo.observer") == ("SUCCESS", None)  # `after` only needs terminal


def test_interrupted_execution(make_app: Factory, app_root: Path) -> None:
    plugin_config(app_root, "demo.configurable", behavior="interrupt")
    app = make_app()
    result = _run(app)
    assert result.status is RunStatus.ABORTED
    assert _state(result, "demo.configurable") == ("ABORTED", "interrupted")
    assert _state(result, "demo.observer") == ("ABORTED", "interrupted")
    assert app.database().get_run(result.run_id)["status"] == "ABORTED"  # type: ignore[index]


def test_optional_subscriber_failure_does_not_affect_run(make_app: Factory) -> None:
    app = make_app()

    def broken(event: Event) -> None:
        raise RuntimeError("progress bar crashed")

    app.bus.subscribe(broken, EventType.PLUGIN_STARTED)
    assert _run(app).status is RunStatus.COMPLETED


def test_persistence_failure_is_surfaced(make_app: Factory) -> None:
    app = make_app()

    def failing(event: Event) -> None:
        raise OSError("database disk image is malformed")

    app.bus.subscribe(failing, EventType.PLUGIN_COMPLETED, critical=True, name="persistence")
    with pytest.raises(EventDeliveryError, match="persistence"):
        _run(app)


def test_runtime_directory_removed_mid_session(make_app: Factory) -> None:
    app = make_app()
    (app.storage.root / "reports").rmdir()
    assert preflight(app, default_registry()).passed
    assert (app.storage.root / "reports").is_dir()


def test_partial_write_leftovers_are_cleaned(make_app: Factory) -> None:
    app = make_app()
    leftover = app.storage.root / "manifests" / ".abc.json.xyz.part"
    leftover.write_text("{ half")
    old = time.time() - 100 * 3600
    os.utime(leftover, (old, old))
    preflight(app, default_registry())
    assert not leftover.exists()


def test_corrupted_configuration_exit_code(
    app_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (app_root / "settings.yaml").write_text("environment: [unterminated")
    assert cli_main(["status"], root=app_root) == 4
    assert "malformed YAML" in capsys.readouterr().err

import json
import logging
from pathlib import Path

import pytest

from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import EventDeliveryError
from photoproof.core.config.validator import LoggingSettings
from photoproof.core.events.bus import EventBus, instrumented
from photoproof.core.logging.setup import attach_event_logging, configure_logging


def test_typed_events_with_utc_timestamps() -> None:
    bus, seen = EventBus(), []
    bus.subscribe(seen.append, EventType.RUN_CREATED)
    bus.emit(EventType.RUN_CREATED, run_id="r1", data={"x": 1})
    bus.emit(EventType.RUN_STARTED, run_id="r1")
    assert [e.type for e in seen] == [EventType.RUN_CREATED]
    assert seen[0].timestamp.utcoffset() is not None and seen[0].to_dict()["data"] == {"x": 1}


def test_optional_subscriber_failure_is_isolated() -> None:
    bus, seen = EventBus(), []

    def broken(event: Event) -> None:
        raise RuntimeError("subscriber bug")

    bus.subscribe(broken)
    bus.subscribe(seen.append)
    bus.emit(EventType.PLUGIN_STARTED)
    assert len(seen) == 1


def test_critical_subscriber_failure_is_surfaced() -> None:
    bus, seen = EventBus(), []

    def persistence(event: Event) -> None:
        raise OSError("disk full")

    bus.subscribe(persistence, critical=True, name="persistence")
    bus.subscribe(seen.append)
    with pytest.raises(EventDeliveryError, match="persistence"):
        bus.emit(EventType.PLUGIN_COMPLETED)
    assert len(seen) == 1  # others still notified


def test_structured_log_and_audit_trail(tmp_path: Path) -> None:
    configure_logging(
        LoggingSettings(
            level="INFO", console_format="text", file_max_bytes=100_000, file_backups=1
        ),
        tmp_path,
    )
    bus = EventBus()
    attach_event_logging(bus, tmp_path)
    bus.emit(EventType.POLICY_OVERRIDE, data={"override": "ignore_blacklist"})
    bus.emit(EventType.PLUGIN_STARTED, plugin_id="p.a")
    for handler in logging.getLogger("photoproof").handlers:
        handler.flush()
    records = [json.loads(line) for line in (tmp_path / "photoproof.log").read_text().splitlines()]
    assert {r["message"] for r in records} >= {"POLICY_OVERRIDE", "PLUGIN_STARTED"}
    audit = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [a["type"] for a in audit] == ["POLICY_OVERRIDE"]


def test_audit_without_runtime_dirs_warns_instead_of_crashing(tmp_path: Path) -> None:
    bus = EventBus()
    attach_event_logging(bus, tmp_path / "missing")
    bus.emit(EventType.REPAIR_APPLIED)  # must not raise


def test_instrumented_decorator(tmp_path: Path) -> None:
    configure_logging(
        LoggingSettings(
            level="DEBUG", console_format="json", file_max_bytes=100_000, file_backups=1
        ),
        tmp_path,
    )

    @instrumented("unit.op")
    def operation(x: int) -> int:
        if x < 0:
            raise ValueError("negative")
        return x * 2

    assert operation(2) == 4
    with pytest.raises(ValueError, match="negative"):
        operation(-1)
    for handler in logging.getLogger("photoproof").handlers:
        handler.flush()
    log = (tmp_path / "photoproof.log").read_text()
    assert '"operation": "unit.op"' in log and "operation failed" in log

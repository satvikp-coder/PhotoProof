"""Lifecycle failures still deterministically release owned runtime resources."""

import pytest

from photoproof.contracts.events import EventType
from photoproof.contracts.exceptions import DatabaseError, EventDeliveryError
from photoproof.core.bootstrap import Application
from photoproof.core.database.store import open_connection_count


def test_critical_stop_subscriber_failure_closes_database_and_logging(make_app, monkeypatch):
    app = make_app()
    db = app.database()
    calls = []
    failures = 0

    def fail_once(event):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("injected stop delivery failure")

    monkeypatch.setattr("photoproof.core.bootstrap.shutdown_logging", lambda: calls.append("log"))
    app.bus.subscribe(fail_once, EventType.APPLICATION_STOPPED, critical=True)
    try:
        with pytest.raises(EventDeliveryError, match="injected stop delivery failure"):
            app.stop()
        assert open_connection_count() == 0
        assert db._closed and app._db is None
        assert calls == ["log"]
        assert app.started is False
        app.stop()
        assert failures == 1  # A repeated stop cannot redeliver the failed event.
    finally:
        # Ensure the regression also cleans up when run against the old implementation.
        app.stop()


def test_database_close_failure_still_closes_logging_and_can_be_retried(make_app, monkeypatch):
    app = make_app()
    queue = app.job_queue()
    db = app.database()
    close = db.close
    calls = []
    attempts = 0

    def fail_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DatabaseError("injected close failure")
        close()

    monkeypatch.setattr(db, "close", fail_once)
    monkeypatch.setattr("photoproof.core.bootstrap.shutdown_logging", lambda: calls.append("log"))
    try:
        with pytest.raises(DatabaseError, match="injected close failure"):
            app.stop()
        assert calls == ["log"]
        assert app._db is db and app._job_queue is queue
        app.stop()
        assert open_connection_count() == 0
        assert app._db is None and app._job_queue is None
        assert attempts == 2 and calls == ["log", "log"]
    finally:
        app.stop()


def test_startup_failure_keeps_fixture_ownership_for_deterministic_cleanup(make_app, monkeypatch):
    start = Application.start

    def fail_after_start(self):
        start(self)
        raise RuntimeError("injected failure after connection opened")

    monkeypatch.setattr(Application, "start", fail_after_start)
    with pytest.raises(RuntimeError, match="injected failure after connection opened"):
        make_app()
    # The autouse connection guard runs after make_app teardown. Without registering
    # ownership before start(), this test leaves an open connection and that guard fails.

"""Structured logging: JSON log file, readable console, and an audit trail.

``ARE/logs/photoproof.log`` — every record as one JSON object per line (rotated).
``ARE/logs/audit.jsonl`` — security-relevant events (overrides, repairs, health failures).
"""

import json
import logging
import logging.handlers
import sys
from pathlib import Path

from photoproof.contracts.events import Event, EventType
from photoproof.core.config.validator import LoggingSettings
from photoproof.core.events.bus import EventBus

_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

AUDITED = (EventType.POLICY_OVERRIDE, EventType.REPAIR_APPLIED, EventType.HEALTH_CHECK_FAILED)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(settings: LoggingSettings, log_dir: Path | None) -> None:
    """Configure the ``photoproof`` logger tree. Safe to call more than once."""
    root = logging.getLogger("photoproof")
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(settings.level)
    root.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING if settings.level != "DEBUG" else logging.DEBUG)
    console.setFormatter(
        JsonFormatter()
        if settings.console_format == "json"
        else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(console)

    if log_dir is not None:
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "photoproof.log",
            maxBytes=settings.file_max_bytes,
            backupCount=settings.file_backups,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)


def attach_event_logging(bus: EventBus, log_dir: Path | None) -> None:
    """Log every event; append audited events to the audit trail."""
    events_log = logging.getLogger("photoproof.events")

    def log_event(event: Event) -> None:
        level = (
            logging.WARNING
            if event.type
            in {EventType.PLUGIN_FAILED, EventType.RUN_ABORTED, EventType.HEALTH_CHECK_FAILED}
            else logging.INFO
        )
        events_log.log(level, event.type.value, extra={"event": event.to_dict()})

    bus.subscribe(log_event, name="structured-logging")

    if log_dir is not None:
        audit_path = log_dir / "audit.jsonl"

        def audit(event: Event) -> None:
            if not audit_path.parent.is_dir():  # before first-run setup: say so, do not crash
                logging.getLogger("photoproof.audit").warning(
                    "audit trail unavailable (runtime directories missing): %s", event.type.value
                )
                return
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), default=str, sort_keys=True) + "\n")

        # The audit trail is security-relevant: losing an entry must be surfaced.
        bus.subscribe(audit, *AUDITED, critical=True, name="audit-trail")

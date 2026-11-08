"""Structured logging: JSON log file, readable console, and an audit trail.

``ARE/logs/photoproof.log`` — every record as one JSON object per line (rotated).
``ARE/logs/audit.jsonl`` — security-relevant events (overrides, repairs, health failures).

The audit trail is hash-chained: every record carries ``seq``, ``prev_sha256`` and
``sha256`` (the SHA-256 of the canonical record).  Any modification, deletion or
reordering of entries is detectable by the ``audit.integrity`` health check.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import sys
from pathlib import Path

from photoproof.contracts.events import Event, EventType
from photoproof.core.config.validator import LoggingSettings
from photoproof.core.events.bus import EventBus

_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

# Events written to the append-only audit trail.  RUN_COMPLETED and RUN_ABORTED carry
# the manifest hash so the chain anchors every finished run.
AUDITED = (
    EventType.POLICY_OVERRIDE,
    EventType.INPUT_REJECTED,
    EventType.REPAIR_APPLIED,
    EventType.HEALTH_CHECK_FAILED,
    EventType.RUN_COMPLETED,
    EventType.RUN_ABORTED,
)


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


def _canonical(record: dict[str, object]) -> str:
    """Canonical JSON for hashing: sorted keys, no trailing whitespace."""
    return json.dumps(record, default=str, sort_keys=True, separators=(",", ":"))


def _record_sha256(record: dict[str, object]) -> str:
    return hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()


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


def shutdown_logging() -> None:
    """Close the file handlers (releases file locks on Windows)."""
    root = logging.getLogger("photoproof")
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            root.removeHandler(handler)
            handler.close()


def _last_chain_state(audit_path: Path) -> tuple[int, str]:
    """Recover ``(seq, prev_sha256)`` from the tail of the audit file.

    Returns ``(0, "")`` for an empty or missing file.  A partial final line is
    detected and reported (never silently repaired): its hash becomes the chain
    anchor, so truncation after a partial write is detectable.
    """
    if not audit_path.is_file():
        return 0, ""
    last_seq = 0
    last_sha = ""
    partial = False
    with audit_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                partial = True
                break
            last_seq = int(record.get("seq", 0))
            last_sha = record.get("sha256", "")
    if partial:
        logging.getLogger("photoproof.audit").warning(
            "audit trail ends with a partial line; chain continues from the last valid record"
        )
    return last_seq, last_sha


def attach_event_logging(bus: EventBus, log_dir: Path | None) -> None:
    """Log every event; append hash-chained audited events to the audit trail."""
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

    if log_dir is None:
        return

    audit_path = log_dir / "audit.jsonl"
    seq: int = 0
    prev_sha: str = ""
    recovered_seq, recovered_sha = _last_chain_state(audit_path)
    seq = recovered_seq
    prev_sha = recovered_sha

    def audit(event: Event) -> None:
        nonlocal seq, prev_sha
        if not audit_path.parent.is_dir():  # before first-run setup: say so, do not crash
            logging.getLogger("photoproof.audit").warning(
                "audit trail unavailable (runtime directories missing): %s", event.type.value
            )
            return
        record = event.to_dict()
        record["seq"] = seq + 1
        record["prev_sha256"] = prev_sha
        record["sha256"] = _record_sha256(record)
        line = json.dumps(record, default=str, sort_keys=True) + "\n"
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        seq = int(record["seq"])  # type: ignore[call-overload]  # we just wrote an int
        prev_sha = str(record["sha256"])

    # The audit trail is security-relevant: losing an entry must be surfaced.
    bus.subscribe(audit, *AUDITED, critical=True, name="audit-trail")

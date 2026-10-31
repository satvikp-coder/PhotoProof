"""Privacy boundaries cover formatted text, extras, exceptions and audit sinks."""

import json
import logging
from pathlib import Path

import pytest

from photoproof.contracts.events import EventType
from photoproof.core.config.validator import LoggingSettings
from photoproof.core.events.bus import EventBus
from photoproof.core.logging.audit import append_audit
from photoproof.core.logging.setup import attach_event_logging, configure_logging, shutdown_logging

PRIVATE = "untrusted \u2603 personal photo.jpg"
GPS = "51.987654, -0.123456"


@pytest.mark.parametrize("console_format", ["text", "json"])
def test_formatted_extras_exception_and_event_privacy(
    tmp_path: Path, capsys, console_format: str
) -> None:  # type: ignore[no-untyped-def]
    settings = LoggingSettings(
        level="DEBUG", console_format=console_format, file_max_bytes=10000, file_backups=1
    )
    configure_logging(settings, tmp_path)
    try:
        log = logging.getLogger("photoproof.privacytest")
        log.warning(
            "failed input %s at %s",
            PRIVATE,
            GPS,
            extra={
                "original_name": PRIVATE,
                "details": {"GPSLatitude": GPS, "exif": {"filename": PRIVATE}},
                PRIVATE: GPS,
            },
        )
        try:
            raise ValueError(f"cannot decode {PRIVATE}, GPS={GPS}")
        except ValueError:
            log.exception("decoder failed %s", PRIVATE)
        # A formatter change must not expose raw args/exc_info/standard path fields.
        for handler in logging.getLogger("photoproof").handlers:
            handler.setFormatter(
                logging.Formatter("%(message)s %(args)s %(exc_text)s %(pathname)s")
            )
        log.warning("%s", PRIVATE)
        bus = EventBus()
        attach_event_logging(bus, tmp_path)
        event = bus.emit(
            EventType.INPUT_REJECTED,
            data={
                "original_name": PRIVATE,
                "path": PRIVATE,
                "GPS": GPS,
                "metadata": {"GPS": GPS},
                "details": PRIVATE,
                "sha256": "a" * 64,
            },
        )
        # Persistence and live events retain their original contract; only sinks sanitize.
        assert event.data["original_name"] == PRIVATE
        for handler in logging.getLogger("photoproof").handlers:
            handler.flush()
        text = (tmp_path / "photoproof.log").read_text(encoding="utf-8")
        audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        console = capsys.readouterr().err
        for output in (text, audit, console):
            assert PRIVATE not in output and "personal photo" not in output
            assert "51.987654" not in output and "-0.123456" not in output
            assert "private-sha256:" in output
        assert "a" * 64 in audit and "INPUT_REJECTED" in audit
        assert json.loads(audit.splitlines()[-1])["data"]["metadata"] == "[redacted]"
    finally:
        shutdown_logging()


def test_audit_writer_independently_sanitizes(tmp_path: Path) -> None:
    target = tmp_path / "audit.jsonl"
    append_audit(
        target,
        {"original_name": PRIVATE, "gps": GPS, "details": {PRIVATE: GPS}, "sha256": "b" * 64},
    )
    text = target.read_text(encoding="utf-8")
    assert PRIVATE not in text and "51.987654" not in text
    assert "b" * 64 in text
    assert json.loads(text)["gps"] == "[redacted]"


@pytest.mark.parametrize("name", ["run", "evidence", "intent", "private no extension", "safe.jpg"])
def test_filenames_never_bypass_using_safe_vocabulary(tmp_path: Path, name: str) -> None:
    target = tmp_path / "audit.jsonl"
    append_audit(target, {"original_name": name})
    assert json.loads(target.read_text())["original_name"].startswith("private-sha256:")


def test_filter_cannot_be_bypassed_with_forged_sanitized_flag(tmp_path: Path) -> None:
    configure_logging(
        LoggingSettings(level="DEBUG", console_format="text", file_max_bytes=10000, file_backups=1),
        tmp_path,
    )
    try:
        logging.getLogger("photoproof").error(PRIVATE, extra={"_privacy_sanitized": True})
        for handler in logging.getLogger("photoproof").handlers:
            handler.flush()
        assert PRIVATE not in (tmp_path / "photoproof.log").read_text(encoding="utf-8")
    finally:
        shutdown_logging()

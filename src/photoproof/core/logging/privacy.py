"""Privacy boundary for diagnostics: unknown text is fingerprinted, never guessed safe.

Filenames need not have extensions and GPS can occur in arbitrary exception strings.
An allowlist, not a filename/GPS regex, prevents those strings reaching log sinks.
Evidence hashes and the original in-process event objects are never modified.
"""

import hashlib
import logging
import re
from collections.abc import Mapping

from photoproof.contracts.events import EventType

# Only code-defined operation names registered by @instrumented may remain readable.
_OPERATIONS: set[str] = set()
_PLUGINS: set[str] = set()
_SAFE_TEXT = {event.value for event in EventType} | {
    "intent",
    "deleted",
    "failed",
    "complete",
    "cancelled",
    "pending",
    "cache",
    "artifact",
    "run",
    "report",
    "evidence",
    "operator_delete",
    "retention",
    "ok",
    "warning",
    "error",
    "critical",
    "safe",
    "controlled",
    "manual",
    "ignore_blacklist",
    "local_cli",
    "web",
    "resource_limit",
    "permission_denied",
    "storage_error",
    "audit_error",
    "operation failed",
    "operation completed",
    "invalidating cache entry",
    "optional event subscriber failed",
}
_KEYS = {
    "type",
    "timestamp",
    "run_id",
    "plugin_id",
    "data",
    "event",
    "outcome",
    "data_class",
    "identity_sha256",
    "sha256",
    "record_id",
    "operation_id",
    "reason",
    "size",
    "reclaimed_bytes",
    "count",
    "index",
    "worker",
    "duration_ms",
    "operation",
    "error",
    "failure_category",
    "message",
    "filename",
    "original_name",
    "path",
    "details",
    "metadata",
    "exif",
    "gps",
    "latitude",
    "longitude",
    "exception",
    "version",
    "override",
    "origin",
    "bypassed_plugins",
    "status",
    "cached",
    "cache_hit",
    "manifest_sha256",
    "role",
    "detected_format",
    "check_id",
    "severity",
    "changes",
    "action",
    "verified",
    "records",
    "removed",
    "classes",
    "phase",
    "with_evidence",
    "files",
    "schema_version",
}
_HASH_KEYS = {
    "sha256",
    "identity_sha256",
    "run_id",
    "record_id",
    "operation_id",
    "cache_key",
    "manifest_sha256",
}
_NUMBER_KEYS = {
    "size",
    "reclaimed_bytes",
    "count",
    "index",
    "worker",
    "duration_ms",
    "schema_version",
    "records",
    "removed",
}
_PRIVATE = re.compile(r"gps|latitude|longitude|exif|metadata|secret|password|token|email", re.I)


def fingerprint(value: object) -> str:
    """One-way correlation token, not a promise of anonymity against dictionary guesses."""
    return (
        "private-sha256:"
        + hashlib.sha256(str(value).encode("utf-8", "backslashreplace")).hexdigest()
    )


def register_operation(name: str) -> None:
    """Trust an operation label supplied in application source, never from configuration."""
    _OPERATIONS.add(name)


def register_plugin_ids(ids: set[str] | frozenset[str]) -> None:
    """Trust IDs from the validated installed-code registry, not uploaded names."""
    _PLUGINS.update(ids)


def sanitize(value: object, *, key: str = "") -> object:
    """Return a new log-safe value; never mutate the source event or evidence contract."""
    if _PRIVATE.search(key):
        return "[redacted]"
    if key.lower() in {
        "filename",
        "original_name",
        "declared_name",
        "name",
        "path",
        "pathname",
        "funcName".lower(),
    }:
        if isinstance(value, str) and re.fullmatch(r"private-sha256:[0-9a-f]{64}", value):
            return value
        return fingerprint(value) if value is not None else None
    if isinstance(value, Mapping):
        return {
            (
                str(k)
                if str(k) in _KEYS or re.fullmatch(r"private-sha256:[0-9a-f]{64}", str(k))
                else fingerprint(k)
            ): sanitize(v, key=str(k))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(child, key=key) for child in value]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if key in _NUMBER_KEYS else fingerprint(value)
    if isinstance(value, str):
        if key in _HASH_KEYS and re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{64})", value):
            return value
        if key == "timestamp" and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+[+Z-][0-9:]*", value
        ):
            return value
        if key in {"plugin_id", "bypassed_plugins"} and value in _PLUGINS:
            return value
        if key == "operation" and value in _OPERATIONS:
            return value
        if value in _SAFE_TEXT or (
            value.startswith("private-sha256:")
            and re.fullmatch(r"private-sha256:[0-9a-f]{64}", value)
        ):
            return value
        return fingerprint(value)
    return fingerprint(value)


class PrivacyFilter(logging.Filter):
    """Sanitize formatted messages, extras and exception paths before any handler formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = sanitize(message)
        record.args = ()
        if record.exc_info is not None:
            record.exc_text = fingerprint(
                record.exc_info[0].__name__ if record.exc_info[0] else "exception"
            )
        elif record.exc_text:
            record.exc_text = str(sanitize(record.exc_text))
        if record.name != "photoproof":
            record.name = str(sanitize(record.name))
        record.threadName = str(sanitize(record.threadName))
        record.processName = str(sanitize(record.processName))
        record.exc_info = None
        record.stack_info = None
        standard = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "lineno",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "exc_info",
            "exc_text",
            "stack_info",
            "taskName",
        }
        for key, value in list(vars(record).items()):
            if key not in standard:
                clean_key = (
                    key
                    if key in _KEYS
                    or key in {"pathname", "module", "funcName", "message", "asctime"}
                    else fingerprint(key)
                )
                if clean_key != key:
                    delattr(record, key)
                setattr(record, clean_key, sanitize(value, key=key))
        return True

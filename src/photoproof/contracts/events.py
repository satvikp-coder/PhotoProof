"""Typed application events dispatched through the in-process event bus."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType


class EventType(StrEnum):
    APPLICATION_STARTED = "APPLICATION_STARTED"
    APPLICATION_STOPPED = "APPLICATION_STOPPED"
    RUN_CREATED = "RUN_CREATED"
    RUN_STARTED = "RUN_STARTED"
    PLUGIN_DISCOVERED = "PLUGIN_DISCOVERED"
    PLUGIN_STARTED = "PLUGIN_STARTED"
    PLUGIN_COMPLETED = "PLUGIN_COMPLETED"
    PLUGIN_FAILED = "PLUGIN_FAILED"
    PLUGIN_SKIPPED = "PLUGIN_SKIPPED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    CACHE_HIT = "CACHE_HIT"
    CACHE_MISS = "CACHE_MISS"
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    HOUSEKEEPING_APPLIED = "HOUSEKEEPING_APPLIED"
    REPAIR_APPLIED = "REPAIR_APPLIED"
    POLICY_OVERRIDE = "POLICY_OVERRIDE"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_ABORTED = "RUN_ABORTED"
    INPUT_REJECTED = "INPUT_REJECTED"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Event:
    type: EventType
    run_id: str | None = None
    plugin_id: str | None = None
    data: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    timestamp: datetime = field(default_factory=_utcnow)  # always timezone-aware UTC

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "run_id": self.run_id,
            "plugin_id": self.plugin_id,
            "data": dict(self.data),
        }

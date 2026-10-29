"""Synchronous in-process event bus.

Subscribers are isolated: an exception in an optional subscriber is logged and the
remaining subscribers still run. A *critical* subscriber (e.g. state persistence)
that fails raises ``EventDeliveryError`` after the others have been notified, so the
failure is never silent.
"""

import functools
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import ParamSpec, TypeVar

from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import EventDeliveryError

Handler = Callable[[Event], None]
P = ParamSpec("P")
R = TypeVar("R")

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Subscription:
    handler: Handler
    types: frozenset[EventType] | None
    critical: bool
    name: str


class EventBus:
    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []

    def subscribe(
        self,
        handler: Handler,
        *types: EventType,
        critical: bool = False,
        name: str | None = None,
    ) -> None:
        """Subscribe to the given event types (all types when none are given)."""
        self._subscriptions.append(
            _Subscription(
                handler,
                frozenset(types) or None,
                critical,
                name or str(getattr(handler, "__qualname__", repr(handler))),
            )
        )

    def publish(self, event: Event) -> None:
        critical_failures: list[str] = []
        for sub in self._subscriptions:
            if sub.types is not None and event.type not in sub.types:
                continue
            try:
                sub.handler(event)
            except Exception as error:  # noqa: BLE001 - subscriber isolation is the point
                if sub.critical:
                    critical_failures.append(f"{sub.name}: {error!r}")
                else:
                    _log.warning(
                        "optional event subscriber failed",
                        extra={
                            "subscriber": sub.name,
                            "event": event.type.value,
                            "error": repr(error),
                        },
                    )
        if critical_failures:
            raise EventDeliveryError(
                f"critical subscriber failed for {event.type.value}: "
                + "; ".join(critical_failures)
            )

    def emit(
        self,
        type_: EventType,
        *,
        run_id: str | None = None,
        plugin_id: str | None = None,
        data: Mapping[str, object] | None = None,
    ) -> Event:
        event = Event(type_, run_id=run_id, plugin_id=plugin_id, data=dict(data or {}))
        self.publish(event)
        return event


def instrumented(name: str | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Log entry, duration and failure of an internal function as structured records.

    Plugin execution does not rely on this decorator; the executor emits events for
    every plugin centrally.
    """

    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        label = name or func.__qualname__
        logger = logging.getLogger("photoproof.instrumentation")

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except BaseException as error:
                logger.error(
                    "operation failed",
                    extra={
                        "operation": label,
                        "error": repr(error),
                        "duration_ms": round((time.perf_counter() - start) * 1000, 3),
                    },
                )
                raise
            logger.debug(
                "operation completed",
                extra={
                    "operation": label,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 3),
                },
            )
            return result

        return wrapper

    return decorate

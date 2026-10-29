"""Plugin execution state machine.

    PENDING ─┬─> READY ──> RUNNING ─┬─> SUCCESS
             │                      ├─> FAILED
             │                      ├─> TIMEOUT
             │                      ├─> NOT_APPLICABLE   (plugin raised NotApplicableError)
             │                      └─> ABORTED          (interrupted)
             ├─> SKIPPED            (a `requires` dependency did not succeed)
             ├─> NOT_APPLICABLE     (input not applicable, e.g. no reference image)
             └─> ABORTED            (run aborted before this plugin started)
    READY ──> SKIPPED | ABORTED

Terminal states have no outgoing transitions.
"""

import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from photoproof.contracts.enums import ExecutionState as S
from photoproof.contracts.enums import FailureCategory
from photoproof.contracts.exceptions import InvalidTransitionError

TRANSITIONS: Mapping[S, frozenset[S]] = {
    S.PENDING: frozenset({S.READY, S.SKIPPED, S.NOT_APPLICABLE, S.ABORTED}),
    S.READY: frozenset({S.RUNNING, S.SKIPPED, S.ABORTED}),
    S.RUNNING: frozenset({S.SUCCESS, S.FAILED, S.TIMEOUT, S.NOT_APPLICABLE, S.ABORTED}),
    **{state: frozenset() for state in S if state.is_terminal},
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class PluginExecution:
    plugin_id: str
    state: S = S.PENDING
    failure_category: FailureCategory | None = None
    message: str | None = None
    exception_type: str | None = None
    traceback: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    dependency_status: dict[str, str] = field(default_factory=dict)

    def transition(
        self,
        new: S,
        *,
        category: FailureCategory | None = None,
        message: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        if new not in TRANSITIONS[self.state]:
            raise InvalidTransitionError(f"{self.plugin_id}: {self.state} -> {new} is not allowed")
        self.state = new
        if new is S.RUNNING:
            self.started_at = _now()
        if new.is_terminal:
            self.finished_at = _now()
        self.failure_category = category
        self.message = message
        if error is not None:
            self.exception_type = type(error).__qualname__
            self.traceback = "".join(traceback.format_exception(error))[-4000:]

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "failure_category": self.failure_category.value if self.failure_category else None,
            "message": self.message,
            "exception_type": self.exception_type,
            "traceback": self.traceback,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dependency_status": dict(sorted(self.dependency_status.items())),
        }

"""Enumerations shared across the application."""

from enum import StrEnum


class ExecutionState(StrEnum):
    """Lifecycle state of one plugin within one run. See ``core.pipeline.scheduler``."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    TIMEOUT = "TIMEOUT"
    ABORTED = "ABORTED"

    @property
    def is_terminal(self) -> bool:
        return self not in {ExecutionState.PENDING, ExecutionState.READY, ExecutionState.RUNNING}


class FailureCategory(StrEnum):
    EXCEPTION = "exception"
    INVALID_OUTPUT = "invalid_output"
    TIMEOUT = "timeout"
    LOAD_ERROR = "load_error"
    CONFIG_ERROR = "config_error"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INTERRUPTED = "interrupted"
    ABORTED_BY_POLICY = "aborted_by_policy"


class RunStatus(StrEnum):
    """Outcome of a whole analysis run. None of these values is an authenticity verdict."""

    COMPLETED = (
        "COMPLETED"  # every planned plugin reached a terminal state; required ones succeeded
    )
    INCOMPLETE = "INCOMPLETE"  # finished, but some plugins failed/skipped; evidence is partial
    ABORTED = "ABORTED"  # stopped early (required failure under `abort`, or interruption)
    BLOCKED = "BLOCKED"  # policy left the selected profile unable to operate; nothing ran
    NO_DETECTORS = "NO_DETECTORS"  # no forensic plugin is installed and enabled


class AnalysisMode(StrEnum):
    SINGLE = "single"
    COMPARISON = "comparison"


class Severity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(Severity).index(self)


class Repairability(StrEnum):
    SAFE = "safe"  # may be applied automatically
    CONTROLLED = "controlled"  # only on explicit `--repair`
    MANUAL = "manual"  # never automated; a human must act

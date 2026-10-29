"""Plugin executors.

``Executor`` is the interface a future process-isolated or parallel executor will
implement. ``SequentialExecutor`` is the only implementation: it runs plugins one at
a time in plan order, enforces dependency semantics and the failure policy, and
publishes an event for every state change, so instrumentation never depends on
plugin authors.
"""

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.enums import ExecutionState as S
from photoproof.contracts.enums import FailureCategory, RunStatus
from photoproof.contracts.events import EventType
from photoproof.contracts.exceptions import (
    ConfigurationError,
    EventDeliveryError,
    NotApplicableError,
    PluginLoadError,
)
from photoproof.core.events.bus import EventBus
from photoproof.core.pipeline.planner import ExecutionPlan
from photoproof.core.pipeline.registry import RegisteredPlugin
from photoproof.core.pipeline.scheduler import PluginExecution

# (plugin, declared inputs) -> raw outputs. Supplied by the controller, which owns
# configuration, storage and the plugin context.
Invoke = Callable[[RegisteredPlugin, Mapping[str, object]], Mapping[str, object]]


class InvalidOutputError(Exception):
    pass


class _Timeout(Exception):
    pass


@dataclass
class ExecutionReport:
    executions: dict[str, PluginExecution]
    outputs: dict[str, object] = field(default_factory=dict)
    aborted: bool = False

    def status(self, required: frozenset[str]) -> RunStatus:
        if self.aborted:
            return RunStatus.ABORTED
        for pid, ex in self.executions.items():
            if ex.state is S.SUCCESS:
                continue
            if ex.state is S.NOT_APPLICABLE and pid not in required:
                continue
            return RunStatus.INCOMPLETE
        return RunStatus.COMPLETED


class Executor(Protocol):
    def execute(
        self, plan: ExecutionPlan, *, run_id: str, run_inputs: Mapping[str, object], invoke: Invoke
    ) -> ExecutionReport: ...


def normalize_outputs(plugin: RegisteredPlugin, raw: object) -> dict[str, object]:
    """Enforce the declared output contract; return JSON-compatible values."""
    if not isinstance(raw, Mapping):
        raise InvalidOutputError(f"expected a mapping of outputs, got {type(raw).__name__}")
    declared, produced = set(plugin.metadata.outputs), set(raw)
    if declared != produced:
        raise InvalidOutputError(
            f"outputs {sorted(produced)} do not match declared outputs {sorted(declared)}"
        )
    normalized: dict[str, object] = {}
    for name in sorted(raw):
        value = raw[name]
        if isinstance(value, ArtifactRef):
            value = {"artifact": value.model_dump(mode="json")}
        try:
            normalized[name] = json.loads(json.dumps(value, allow_nan=False))
        except (TypeError, ValueError) as error:
            raise InvalidOutputError(
                f"output {name!r} is not JSON-serializable: {error}"
            ) from error
    return normalized


def _call_with_timeout(func: Callable[[], object], timeout: float, name: str) -> object:
    # ponytail: an in-process thread cannot be killed; a timed-out plugin keeps running
    # in the background until the process exits. Process isolation is a backlog item.
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["result"] = func()
        except BaseException as error:  # noqa: BLE001 - re-raised in the calling thread
            box["error"] = error

    thread = threading.Thread(target=target, name=f"plugin:{name}", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise _Timeout
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["result"]


class SequentialExecutor:
    def __init__(
        self,
        *,
        bus: EventBus,
        on_required_failure: Literal["abort", "degrade"],
        default_timeout_seconds: float,
    ) -> None:
        self._bus = bus
        self._on_required_failure = on_required_failure
        self._default_timeout = default_timeout_seconds

    def execute(
        self, plan: ExecutionPlan, *, run_id: str, run_inputs: Mapping[str, object], invoke: Invoke
    ) -> ExecutionReport:
        report = ExecutionReport({pid: PluginExecution(pid) for pid in plan.order})
        abort_category: FailureCategory | None = None
        for pid in plan.order:
            ex = report.executions[pid]
            plugin = plan.nodes[pid]
            meta = plugin.metadata
            ex.dependency_status = {
                dep: report.executions[dep].state.value
                if dep in report.executions
                else "NOT_PLANNED"
                for dep in (*meta.requires, *meta.after)
            }
            if abort_category is not None:
                self._finish(run_id, ex, S.ABORTED, abort_category, "run aborted before start")
                continue
            failed_deps = [d for d in meta.requires if ex.dependency_status[d] != S.SUCCESS.value]
            if failed_deps:
                detail = ", ".join(f"{d}={ex.dependency_status[d]}" for d in failed_deps)
                self._finish(
                    run_id,
                    ex,
                    S.SKIPPED,
                    FailureCategory.DEPENDENCY_UNAVAILABLE,
                    f"required dependencies did not succeed: {detail}",
                )
            elif "input.reference" in meta.inputs and "input.reference" not in run_inputs:
                self._finish(run_id, ex, S.NOT_APPLICABLE, None, "no reference image supplied")
            else:
                inputs = {name: _lookup(name, run_inputs, report.outputs) for name in meta.inputs}
                ex.transition(S.READY)
                interrupted = self._run_one(run_id, plugin, ex, inputs, invoke, report)
                if interrupted:
                    abort_category = FailureCategory.INTERRUPTED
                    report.aborted = True
                    continue
            required_failed = pid in plan.required and ex.state is not S.SUCCESS
            if required_failed and self._on_required_failure == "abort":
                abort_category = FailureCategory.ABORTED_BY_POLICY
                report.aborted = True
        return report

    def _run_one(
        self,
        run_id: str,
        plugin: RegisteredPlugin,
        ex: PluginExecution,
        inputs: Mapping[str, object],
        invoke: Invoke,
        report: ExecutionReport,
    ) -> bool:
        """Run one plugin; return True if execution was interrupted."""
        ex.transition(S.RUNNING)
        self._bus.emit(
            EventType.PLUGIN_STARTED,
            run_id=run_id,
            plugin_id=ex.plugin_id,
            data={"state": ex.state.value, "started_at": ex.started_at},
        )
        timeout = plugin.metadata.timeout_seconds or self._default_timeout
        try:
            raw = _call_with_timeout(lambda: invoke(plugin, inputs), timeout, ex.plugin_id)
            outputs = normalize_outputs(plugin, raw)
        except NotApplicableError as error:
            self._finish(run_id, ex, S.NOT_APPLICABLE, None, str(error))
        except _Timeout:
            self._finish(run_id, ex, S.TIMEOUT, FailureCategory.TIMEOUT, f"exceeded {timeout}s")
        except InvalidOutputError as error:
            self._finish(run_id, ex, S.FAILED, FailureCategory.INVALID_OUTPUT, str(error), error)
        except PluginLoadError as error:
            self._finish(run_id, ex, S.FAILED, FailureCategory.LOAD_ERROR, str(error), error)
        except ConfigurationError as error:
            self._finish(run_id, ex, S.FAILED, FailureCategory.CONFIG_ERROR, str(error), error)
        except EventDeliveryError:
            raise  # critical persistence failure: never treat as a plugin failure
        except KeyboardInterrupt as error:
            self._finish(run_id, ex, S.ABORTED, FailureCategory.INTERRUPTED, "interrupted", error)
            return True
        except Exception as error:  # noqa: BLE001 - plugin isolation
            self._finish(run_id, ex, S.FAILED, FailureCategory.EXCEPTION, repr(error), error)
        else:
            report.outputs.update(outputs)
            self._finish(run_id, ex, S.SUCCESS, None, None)
        return False

    def _finish(
        self,
        run_id: str,
        ex: PluginExecution,
        state: S,
        category: FailureCategory | None,
        message: str | None,
        error: BaseException | None = None,
    ) -> None:
        ex.transition(state, category=category, message=message, error=error)
        event = {
            S.SUCCESS: EventType.PLUGIN_COMPLETED,
            S.FAILED: EventType.PLUGIN_FAILED,
            S.TIMEOUT: EventType.PLUGIN_FAILED,
        }.get(state, EventType.PLUGIN_SKIPPED)
        if state is S.ABORTED and ex.started_at is not None:
            event = EventType.PLUGIN_FAILED
        self._bus.emit(event, run_id=run_id, plugin_id=ex.plugin_id, data=ex.to_dict())


def _lookup(name: str, run_inputs: Mapping[str, object], outputs: Mapping[str, object]) -> object:
    return run_inputs[name] if name in run_inputs else outputs[name]

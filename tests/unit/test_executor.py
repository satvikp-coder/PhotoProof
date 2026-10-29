"""Dependency semantics, failure isolation and the failure policy, without the controller."""

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from helpers import meta, registry_of

from photoproof.contracts.enums import ExecutionState as S
from photoproof.contracts.enums import FailureCategory, RunStatus
from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import NotApplicableError
from photoproof.core.config.policy import DecisionSource, PolicyDecision
from photoproof.core.config.validator import Profile
from photoproof.core.events.bus import EventBus
from photoproof.core.pipeline.executor import ExecutionReport, SequentialExecutor
from photoproof.core.pipeline.planner import ExecutionPlan, plan
from photoproof.core.pipeline.registry import RegisteredPlugin

Behaviour = Callable[[Mapping[str, object]], Mapping[str, object]]


def _ok(pid: str) -> Behaviour:
    return lambda inputs: {pid + ".out": {"from": pid, "inputs": sorted(inputs)}}


def _raise(_: Mapping[str, object]) -> Mapping[str, object]:
    raise RuntimeError("boom")


def _setup(tmp_path: Path, settings, *metas: dict, required: tuple[str, ...] = ()) -> ExecutionPlan:  # type: ignore[no-untyped-def,type-arg]
    registry = registry_of(tmp_path, *metas)
    decisions = {p.id: PolicyDecision(p.id, True, DecisionSource.DEFAULT, "") for p in registry}
    profile = Profile(name="t", description="t", mode="single", required_plugins=required)
    return plan(registry, decisions, profile=profile, settings=settings)


def _run(
    execution_plan: ExecutionPlan,
    behaviours: dict[str, Behaviour],
    *,
    policy: str = "abort",
    run_inputs: Mapping[str, object] | None = None,
) -> tuple[ExecutionReport, list[Event]]:
    bus, events = EventBus(), []
    bus.subscribe(events.append)
    executor = SequentialExecutor(
        bus=bus,
        on_required_failure=policy,  # type: ignore[arg-type]
        default_timeout_seconds=0.3,
    )

    def invoke(plugin: RegisteredPlugin, inputs: Mapping[str, object]) -> Mapping[str, object]:
        return behaviours.get(plugin.id, _ok(plugin.id))(inputs)

    report = executor.execute(
        execution_plan,
        run_id="r1",
        run_inputs=run_inputs or {"input.target": {"sha256": "0" * 64}},
        invoke=invoke,
    )
    return report, events


def _states(report: ExecutionReport) -> dict[str, S]:
    return {pid: ex.state for pid, ex in report.executions.items()}


def test_all_succeed_and_outputs_flow(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    execution_plan = _setup(
        tmp_path,
        settings,
        meta("p.a", priority=0),
        meta("p.b", requires=["p.a"], inputs=["p.a.out"]),
    )
    report, events = _run(execution_plan, {})
    assert _states(report) == {"p.a": S.SUCCESS, "p.b": S.SUCCESS}
    assert report.outputs["p.b.out"] == {"from": "p.b", "inputs": ["p.a.out"]}
    assert report.status(execution_plan.required) is RunStatus.COMPLETED
    started = [e.plugin_id for e in events if e.type is EventType.PLUGIN_STARTED]
    assert started == ["p.a", "p.b"]


def test_optional_failure_isolated_and_dependents_skipped(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    execution_plan = _setup(
        tmp_path,
        settings,
        meta("p.a", priority=0),
        meta("p.b", requires=["p.a"]),
        meta("p.c", requires=["p.b"]),
        meta("p.d"),
    )
    report, events = _run(execution_plan, {"p.a": _raise})
    assert _states(report) == {
        "p.a": S.FAILED,
        "p.b": S.SKIPPED,
        "p.c": S.SKIPPED,
        "p.d": S.SUCCESS,
    }
    failed = report.executions["p.a"]
    assert (
        failed.failure_category is FailureCategory.EXCEPTION
        and failed.exception_type == "RuntimeError"
    )
    assert failed.started_at and failed.finished_at
    assert report.executions["p.c"].dependency_status == {"p.b": "SKIPPED"}
    assert report.status(execution_plan.required) is RunStatus.INCOMPLETE
    assert {e.type for e in events} >= {EventType.PLUGIN_FAILED, EventType.PLUGIN_SKIPPED}


def test_ordering_only_dependency_runs_after_failure(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    execution_plan = _setup(tmp_path, settings, meta("p.a", priority=0), meta("p.b", after=["p.a"]))
    report, _ = _run(execution_plan, {"p.a": _raise})
    assert _states(report) == {"p.a": S.FAILED, "p.b": S.SUCCESS}
    assert report.executions["p.b"].dependency_status == {"p.a": "FAILED"}


def test_required_failure_aborts_remaining(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    execution_plan = _setup(
        tmp_path,
        settings,
        meta("p.a", priority=0, required=True),
        meta("p.b"),
        meta("p.c", priority=20),
    )
    report, _ = _run(execution_plan, {"p.a": _raise})
    assert _states(report) == {"p.a": S.FAILED, "p.b": S.ABORTED, "p.c": S.ABORTED}
    assert report.executions["p.b"].failure_category is FailureCategory.ABORTED_BY_POLICY
    assert report.status(execution_plan.required) is RunStatus.ABORTED


def test_required_failure_degrade_policy(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    execution_plan = _setup(
        tmp_path, settings, meta("p.a", priority=0), meta("p.b"), required=("p.a",)
    )
    report, _ = _run(execution_plan, {"p.a": _raise}, policy="degrade")
    assert _states(report) == {"p.a": S.FAILED, "p.b": S.SUCCESS}
    assert report.status(execution_plan.required) is RunStatus.INCOMPLETE


def test_dependency_not_planned_is_skipped(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    registry = registry_of(tmp_path, meta("p.a", priority=0), meta("p.b", requires=["p.a"]))
    decisions = {
        "p.a": PolicyDecision("p.a", False, DecisionSource.BLACKLIST, "blacklisted"),
        "p.b": PolicyDecision("p.b", True, DecisionSource.DEFAULT, ""),
    }
    execution_plan = plan(
        registry,
        decisions,
        settings=settings,
        profile=Profile(name="t", description="t", mode="single"),
    )
    report, _ = _run(execution_plan, {})
    assert report.executions["p.b"].state is S.SKIPPED
    assert report.executions["p.b"].dependency_status == {"p.a": "NOT_PLANNED"}


def test_invalid_outputs(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    execution_plan = _setup(tmp_path, settings, meta("p.a"), meta("p.b"), meta("p.c"))
    report, _ = _run(
        execution_plan,
        {
            "p.a": lambda _: {"wrong.name": 1},
            "p.b": lambda _: {"p.b.out": float("nan")},
            "p.c": lambda _: ["not", "a", "mapping"],  # type: ignore[dict-item,return-value]
        },
    )
    for pid in ("p.a", "p.b", "p.c"):
        assert report.executions[pid].failure_category is FailureCategory.INVALID_OUTPUT


def test_timeout(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    execution_plan = _setup(tmp_path, settings, meta("p.slow"), meta("p.fast", priority=20))
    report, _ = _run(execution_plan, {"p.slow": lambda _: time.sleep(2) or {}})  # type: ignore[func-returns-value]
    assert _states(report) == {"p.slow": S.TIMEOUT, "p.fast": S.SUCCESS}


def test_not_applicable(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    def declines(_: Mapping[str, object]) -> Mapping[str, object]:
        raise NotApplicableError("not for this input")

    execution_plan = _setup(
        tmp_path, settings, meta("p.a"), meta("p.cmp", inputs=["input.reference"])
    )
    report, _ = _run(execution_plan, {"p.a": declines})
    assert _states(report) == {"p.a": S.NOT_APPLICABLE, "p.cmp": S.NOT_APPLICABLE}
    assert report.status(execution_plan.required) is RunStatus.COMPLETED


def test_interruption_aborts_run(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    def interrupt(_: Mapping[str, object]) -> Mapping[str, object]:
        raise KeyboardInterrupt

    execution_plan = _setup(tmp_path, settings, meta("p.a", priority=0), meta("p.b"))
    report, _ = _run(execution_plan, {"p.a": interrupt})
    assert _states(report) == {"p.a": S.ABORTED, "p.b": S.ABORTED}
    assert report.executions["p.b"].failure_category is FailureCategory.INTERRUPTED
    assert report.status(execution_plan.required) is RunStatus.ABORTED


def test_every_plugin_reaches_terminal_state(tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
    execution_plan = _setup(
        tmp_path,
        settings,
        meta("p.a", priority=0),
        meta("p.b", requires=["p.a"]),
        meta("p.c", after=["p.b"]),
    )
    report, _ = _run(execution_plan, {"p.a": _raise})
    assert all(ex.state.is_terminal and ex.finished_at for ex in report.executions.values())

import pytest

from photoproof.contracts.enums import ExecutionState as S
from photoproof.contracts.exceptions import InvalidTransitionError
from photoproof.core.pipeline.scheduler import TRANSITIONS, PluginExecution


def test_happy_path_records_timestamps() -> None:
    ex = PluginExecution("p.a")
    for state in (S.READY, S.RUNNING, S.SUCCESS):
        ex.transition(state)
    assert ex.started_at and ex.finished_at and ex.state is S.SUCCESS


@pytest.mark.parametrize("terminal", [s for s in S if s.is_terminal])
def test_terminal_states_are_final(terminal: S) -> None:
    assert TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize(
    ("start", "target"),
    [(S.PENDING, S.RUNNING), (S.PENDING, S.SUCCESS), (S.READY, S.SUCCESS), (S.RUNNING, S.READY)],
)
def test_illegal_transitions(start: S, target: S) -> None:
    ex = PluginExecution("p.a", state=start)
    with pytest.raises(InvalidTransitionError):
        ex.transition(target)


def test_failure_records_exception() -> None:
    ex = PluginExecution("p.a", state=S.RUNNING)
    try:
        raise ValueError("boom")
    except ValueError as error:
        ex.transition(S.FAILED, message="boom", error=error)
    assert ex.exception_type == "ValueError" and "boom" in (ex.traceback or "")

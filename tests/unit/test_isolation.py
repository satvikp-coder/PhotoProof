"""Every failure kind, in a real child process, on every platform that can enforce it."""

import os
import pickle
import signal
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import isolation_targets as targets
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.core import isolation
from photoproof.core.bootstrap import Application
from photoproof.core.health import checks
from photoproof.core.health.checks import check_isolation
from photoproof.core.isolation import (
    Failure,
    FailureKind,
    IsolationCapabilities,
    Ok,
    ProcessLimits,
    capabilities,
    run_isolated,
)

GENEROUS = ProcessLimits(timeout_s=60)
FAST_TIMEOUT = ProcessLimits(timeout_s=1.5)
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="POSIX behaviour")
# macOS shows a "Python quit unexpectedly" dialog for each real crash, so on a developer's
# Mac these run only in CI, which still covers them on macOS.
REAL_CRASH = pytest.mark.skipif(
    sys.platform == "darwin" and not os.environ.get("CI"),
    reason="would open a macOS crash dialog; runs in CI",
)
CPU_ENFORCED = pytest.mark.skipif(
    capabilities().cpu is None, reason="CPU time cannot be limited here; see capabilities()"
)


def expect_failure(result: object, kind: FailureKind) -> Failure:
    assert isinstance(result, Failure), result
    assert result.kind is kind, result
    return result


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ------------------------------------------------------------------------- successes


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        7,
        2.5,
        "text",
        "名前",
        b"\x00\xff" * 10,
        [1, [2, [3]]],
        (1, "a"),
        {"a": {"b": [1, 2, b"x"]}},
        {1, 2},
    ],
)
def test_plain_data_round_trips(value: object) -> None:
    assert run_isolated(targets.echo, value, limits=GENEROUS) == Ok(value)


def test_function_receives_the_payload() -> None:
    assert run_isolated(targets.double, 21, limits=GENEROUS) == Ok(42)


def test_large_result_within_the_cap_is_returned() -> None:
    size = 8 << 20  # larger than any pipe buffer
    assert run_isolated(targets.large_result, size, limits=GENEROUS) == Ok(b"x" * size)


def test_large_payload_within_the_cap_is_delivered() -> None:
    data = b"y" * (8 << 20)
    assert run_isolated(targets.echo, data, limits=GENEROUS) == Ok(data)


# -------------------------------------------------------------------------- failures


def test_exception_is_reported_with_type_and_message() -> None:
    failure = expect_failure(
        run_isolated(targets.raise_value_error, None, limits=GENEROUS), FailureKind.RAISED
    )
    assert "ValueError: deliberate failure" in failure.detail


@REAL_CRASH
def test_segfault_is_reported_as_crashed() -> None:
    expect_failure(run_isolated(targets.segfault, None, limits=GENEROUS), FailureKind.CRASHED)


def test_abrupt_death_is_reported_as_crashed() -> None:
    expect_failure(
        run_isolated(targets.killed_abruptly, None, limits=GENEROUS), FailureKind.CRASHED
    )


@POSIX_ONLY
def test_exit_signal_is_named() -> None:
    failure = isolation._describe_exit(-signal.SIGSEGV)
    assert (failure.kind, failure.detail) == (FailureKind.CRASHED, "killed by SIGSEGV")


@POSIX_ONLY
def test_cpu_signal_means_the_cpu_limit() -> None:
    assert isolation._describe_exit(-signal.SIGXCPU).kind is FailureKind.CPU_LIMIT


def test_hang_is_stopped_at_the_deadline() -> None:
    start = time.monotonic()
    expect_failure(run_isolated(targets.hang, None, limits=FAST_TIMEOUT), FailureKind.TIMEOUT)
    assert time.monotonic() - start < 30


@POSIX_ONLY
def test_child_ignoring_sigterm_is_killed() -> None:
    start = time.monotonic()
    expect_failure(
        run_isolated(targets.ignore_sigterm_and_hang, None, limits=FAST_TIMEOUT),
        FailureKind.TIMEOUT,
    )
    assert time.monotonic() - start < 30


def test_unbounded_allocation_hits_the_memory_limit() -> None:
    limits = ProcessLimits(timeout_s=60, memory_mb=256)
    failure = expect_failure(
        run_isolated(targets.allocate_forever, None, limits=limits), FailureKind.MEMORY_LIMIT
    )
    assert failure.detail


def test_memory_error_is_reported_as_memory_limit() -> None:
    expect_failure(
        run_isolated(targets.raise_memory_error, None, limits=GENEROUS), FailureKind.MEMORY_LIMIT
    )


@CPU_ENFORCED
def test_busy_loop_hits_the_cpu_limit() -> None:
    limits = ProcessLimits(timeout_s=60, cpu_s=1)
    expect_failure(run_isolated(targets.spin, None, limits=limits), FailureKind.CPU_LIMIT)


def test_cpu_limit_does_not_stop_a_sleeping_child_early() -> None:
    limits = ProcessLimits(timeout_s=1.5, cpu_s=30)
    expect_failure(run_isolated(targets.hang, None, limits=limits), FailureKind.TIMEOUT)


def test_oversized_result_is_refused() -> None:
    limits = ProcessLimits(timeout_s=60, max_result_bytes=1024)
    failure = expect_failure(
        run_isolated(targets.large_result, 1 << 20, limits=limits), FailureKind.INVALID_RESULT
    )
    assert "size limit" in failure.detail


def test_result_exactly_at_a_generous_cap_is_allowed() -> None:
    limits = ProcessLimits(timeout_s=60, max_result_bytes=1024)
    assert run_isolated(targets.large_result, 100, limits=limits) == Ok(b"x" * 100)


def test_oversized_payload_is_rejected_before_starting_a_child() -> None:
    limits = ProcessLimits(timeout_s=60, max_payload_bytes=10)
    with pytest.raises(InputRejectedError, match="over the limit"):
        run_isolated(targets.echo, b"y" * 100, limits=limits)


def test_result_that_cannot_be_pickled_is_invalid() -> None:
    expect_failure(
        run_isolated(targets.unpicklable_result, None, limits=GENEROUS), FailureKind.INVALID_RESULT
    )


def test_exit_without_a_result_is_crashed() -> None:
    failure = expect_failure(
        run_isolated(targets.exit_cleanly_without_result, None, limits=GENEROUS),
        FailureKind.CRASHED,
    )
    assert "code 0" in failure.detail


def test_nonzero_exit_is_crashed_with_the_code() -> None:
    failure = expect_failure(
        run_isolated(targets.exit_with_code, 3, limits=GENEROUS), FailureKind.CRASHED
    )
    assert "code 3" in failure.detail


# ---------------------------------------------------------- hostile child containment


def test_result_holding_a_class_is_refused() -> None:
    failure = expect_failure(
        run_isolated(targets.class_result, None, limits=GENEROUS), FailureKind.INVALID_RESULT
    )
    assert "not plain data" in failure.detail


@POSIX_ONLY
def test_forged_pickle_from_the_child_does_not_run_code(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    expect_failure(
        run_isolated(targets.forged_pickle_result, str(marker), limits=GENEROUS),
        FailureKind.INVALID_RESULT,
    )
    assert not marker.exists()


@POSIX_ONLY
def test_half_a_message_cannot_defeat_the_deadline() -> None:
    start = time.monotonic()
    expect_failure(
        run_isolated(targets.half_message_then_hang, None, limits=FAST_TIMEOUT),
        FailureKind.TIMEOUT,
    )
    assert time.monotonic() - start < 30


# ------------------------------------------------------------------ child environment


def test_child_gets_a_clean_environment_and_a_temporary_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOTOPROOF_TEST_SECRET", "secret")
    result = run_isolated(targets.environment_and_directory, None, limits=GENEROUS)
    assert isinstance(result, Ok)
    assert "PHOTOPROOF_TEST_SECRET" not in result.value["keys"]
    assert result.value["cwd"] != os.getcwd()
    assert not Path(result.value["cwd"]).exists()  # removed afterwards


def test_child_workdirs_are_removed_after_a_crash() -> None:
    before = set(Path(tempfile.gettempdir()).glob("photoproof-child-*"))
    run_isolated(targets.killed_abruptly, None, limits=GENEROUS)
    assert set(Path(tempfile.gettempdir()).glob("photoproof-child-*")) <= before


@POSIX_ONLY
def test_descendants_are_killed_with_the_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    limits = ProcessLimits(timeout_s=3)
    expect_failure(
        run_isolated(targets.start_grandchild_and_hang, str(pid_file), limits=limits),
        FailureKind.TIMEOUT,
    )
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while process_exists(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not process_exists(grandchild)


def test_no_child_processes_are_left_behind() -> None:
    import multiprocessing

    run_isolated(targets.killed_abruptly, None, limits=GENEROUS)
    run_isolated(targets.hang, None, limits=FAST_TIMEOUT)
    run_isolated(targets.double, 1, limits=GENEROUS)
    assert multiprocessing.active_children() == []


# ------------------------------------------------------------------ parent robustness


def test_interrupting_the_parent_still_stops_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import multiprocessing

    def interrupted(*_: object, **__: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(isolation, "_wait", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_isolated(targets.hang, None, limits=GENEROUS)
    assert multiprocessing.active_children() == []


def test_failure_to_start_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import multiprocessing.context

    def refuse(_: object) -> None:
        raise OSError("Resource temporarily unavailable")

    monkeypatch.setattr(multiprocessing.context.SpawnProcess, "start", refuse)
    failure = expect_failure(run_isolated(targets.double, 1, limits=GENEROUS), FailureKind.CRASHED)
    assert "could not start" in failure.detail


def test_failure_to_apply_limits_does_not_run_the_work(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object) -> None:
        raise OSError("cannot create job")

    monkeypatch.setattr(isolation, "_contain", refuse)
    failure = expect_failure(run_isolated(targets.double, 1, limits=GENEROUS), FailureKind.CRASHED)
    assert "could not apply the limits" in failure.detail


def test_lambda_is_a_programming_error_in_the_parent() -> None:
    with pytest.raises((pickle.PicklingError, AttributeError)):
        run_isolated(lambda x: x, 1, limits=GENEROUS)


def test_concurrent_calls_do_not_interfere() -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda n: run_isolated(targets.double, n, limits=GENEROUS), range(4))
        )
    assert results == [Ok(n * 2) for n in range(4)]


# ------------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "arguments",
    [
        {"timeout_s": 0},
        {"timeout_s": -1},
        {"timeout_s": float("inf")},
        {"timeout_s": float("nan")},
        {"timeout_s": 1, "memory_mb": 0},
        {"timeout_s": 1, "cpu_s": -1},
        {"timeout_s": 1, "max_payload_bytes": 0},
        {"timeout_s": 1, "max_result_bytes": 0},
    ],
)
def test_invalid_limits_are_rejected(arguments: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="must be positive and finite"):
        ProcessLimits(**arguments)


def test_doctor_reports_the_capabilities(make_app: Callable[..., Application]) -> None:
    result = check_isolation(make_app())
    assert result.ok and result.check_id == "isolation.capabilities"
    assert "memory" in result.message and "cpu" in result.message


def test_doctor_names_a_limit_it_cannot_enforce(
    make_app: Callable[..., Application], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        checks, "capabilities", lambda: IsolationCapabilities(memory="Job Object", cpu=None)
    )
    assert "cpu: not enforceable" in check_isolation(make_app()).message


def test_capabilities_match_this_platform() -> None:
    found = capabilities()
    assert found.wall_clock
    expected = {"linux": "RLIMIT_AS", "darwin": "RSS watchdog (polling)", "win32": "Job Object"}
    if sys.platform in expected:
        assert found.memory == expected[sys.platform]
    assert (found.cpu is None) == (sys.platform == "win32")

"""Run a Python callable in a spawned child process with resource limits.

The child may be running native code on hostile input, so everything it sends back is
treated as untrusted. Replies use ``plaindata``, not pickle, so they can't run code in the
parent or make it allocate more than the reply's size allows.

Enforceable limits differ per platform; ``capabilities`` reports them:

    ========  ======================  ====================  =========
    Platform  Memory                  CPU                   Wall clock
    ========  ======================  ====================  =========
    Linux     RLIMIT_AS               RLIMIT_CPU            yes
    macOS     RSS watchdog (polling)  RLIMIT_CPU            yes
    Windows   Job Object              not enforced          yes
    ========  ======================  ====================  =========

macOS ignores RLIMIT_AS, so its watchdog can overshoot between polls.
"""

import contextlib
import enum
import math
import multiprocessing
import os
import pickle  # nosec B403
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.core import plaindata
from photoproof.core.plaindata import PlainDataError

if sys.platform != "win32":
    import resource

_MEBIBYTE = 1 << 20
_DEFAULT_MAX_TRANSFER_BYTES = 1024 * _MEBIBYTE
_MAX_DETAIL_CHARS = 2000
# start() polls every other child in multiprocessing's shared table, so a concurrent close() of
# one of them makes it fail with "the handle is invalid" on Windows.
_child_table_lock = threading.Lock()
_POLL_SECONDS = 0.01
_WATCHDOG_SECONDS = 0.1
_TERMINATE_GRACE_SECONDS = 0.5
_KILL_WAIT_SECONDS = 5.0
_EXIT_GRACE_SECONDS = 5.0  # time to collect a result already in the pipe after the child exits
_SETUP_FAILED_EXIT_CODE = 70  # the child could not apply its limits, so it did no work
_WATCHER_WAIT_SECONDS = 2.0
_WINDOWS_ENVIRONMENT = ("SYSTEMROOT", "WINDIR")  # Windows cannot start some services without


class FailureKind(enum.StrEnum):
    TIMEOUT = "timeout"
    MEMORY_LIMIT = "memory_limit"
    CPU_LIMIT = "cpu_limit"
    CRASHED = "crashed"
    RAISED = "raised"
    INVALID_RESULT = "invalid_result"  # oversized, or not plain data


@dataclass(frozen=True)
class ProcessLimits:
    """Limits for one child. ``None`` means that limit is not applied."""

    timeout_s: float
    memory_mb: int | None = None
    cpu_s: float | None = None
    max_payload_bytes: int = _DEFAULT_MAX_TRANSFER_BYTES
    max_result_bytes: int = _DEFAULT_MAX_TRANSFER_BYTES

    def __post_init__(self) -> None:
        for name in ("timeout_s", "memory_mb", "cpu_s", "max_payload_bytes", "max_result_bytes"):
            value = getattr(self, name)
            if value is not None and not (value > 0 and math.isfinite(value)):
                raise ValueError(f"ProcessLimits.{name} must be positive and finite, got {value}")


@dataclass(frozen=True)
class Ok:
    value: Any


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    detail: str


IsolatedResult = Ok | Failure


@dataclass(frozen=True)
class IsolationCapabilities:
    """How each limit is enforced here. ``None`` means it cannot be enforced."""

    memory: str | None
    cpu: str | None
    wall_clock: str = "parent deadline, then terminate and kill"


def capabilities() -> IsolationCapabilities:
    if sys.platform == "win32":
        return IsolationCapabilities(memory="Job Object", cpu=None)
    if sys.platform == "darwin":
        return IsolationCapabilities(memory="RSS watchdog (polling)", cpu="RLIMIT_CPU")
    return IsolationCapabilities(memory="RLIMIT_AS", cpu="RLIMIT_CPU")


def run_isolated(
    func: Callable[[Any], Any], payload: object, *, limits: ProcessLimits
) -> IsolatedResult:
    """Call ``func(payload)`` in a new process and return its result or a typed failure.

    ``func`` must be importable, so it can be pickled by reference. ``payload`` comes from
    the trusted parent; untrusted data in it should be bytes. The result must be plain data
    (``None``, numbers, strings, bytes, and lists, tuples, dicts and sets of those), with at
    most ``plaindata.MAX_NODES`` objects. Large data should be bytes.

    Raises ``InputRejectedError`` if the payload is over ``limits.max_payload_bytes``.
    Everything that goes wrong in the child is returned as a ``Failure``. If a limit cannot
    be applied, the child does not run. The child and its descendants are gone on return.
    """
    data = pickle.dumps(payload, protocol=5)
    if len(data) > limits.max_payload_bytes:
        raise InputRejectedError(
            f"payload is {len(data)} bytes, over the limit of {limits.max_payload_bytes} bytes"
        )
    with tempfile.TemporaryDirectory(
        prefix="photoproof-child-", ignore_cleanup_errors=True
    ) as workdir:
        return _supervise(func, data, limits, workdir)


def _supervise(
    func: Callable[[Any], Any], data: bytes, limits: ProcessLimits, workdir: str
) -> IsolatedResult:
    context = multiprocessing.get_context("spawn")  # never fork: the parent may have threads
    ours, theirs = context.Pipe(duplex=True)
    process = context.Process(target=_child_main, args=(theirs, func, limits, workdir), daemon=True)
    job = None
    exchange = None
    try:
        try:
            with _child_table_lock:
                process.start()
        except OSError as error:
            return Failure(FailureKind.CRASHED, f"could not start the child: {error}")
        finally:
            theirs.close()
        try:
            job = _contain(process, limits)  # before the child receives any work
        except OSError as error:
            return Failure(FailureKind.CRASHED, f"could not apply the limits: {error}")
        exchange = _Exchange(ours, data, limits.max_result_bytes)
        exchange.start()
        failure = _wait(exchange, process, limits)
        if failure is not None:
            return failure
        return _interpret(exchange, process)
    finally:
        _stop(process)
        if exchange is not None:
            exchange.join(_EXIT_GRACE_SECONDS)  # the child is gone, so its pipe has reached EOF
        ours.close()
        _release(job)


class _Exchange(threading.Thread):
    """Sends the payload and receives the reply off the main thread.

    Both block on a pipe that a hostile child controls. On the main thread, a child that
    sends half a message and then hangs would defeat the deadline.
    """

    def __init__(self, connection: Connection, data: bytes, max_result_bytes: int) -> None:
        super().__init__(daemon=True, name="photoproof-isolation")
        self._connection = connection
        self._data = data
        self._max_result_bytes = max_result_bytes
        self.reply: bytes | None = None
        self.oversized = False
        self.pipe_error: str | None = None

    def run(self) -> None:
        # A child that ran out of memory stops reading and replies. Its reply says why, so a
        # broken pipe while sending must not stop us from reading it.
        with contextlib.suppress(OSError):
            self._connection.send_bytes(self._data)
        try:
            self.reply = self._connection.recv_bytes(self._max_result_bytes)
        except EOFError:
            pass  # the child exited without replying; its exit code says why
        except OSError as error:
            # multiprocessing reports a message over the size cap only through this text.
            self.oversized = "bad message length" in str(error)
            self.pipe_error = str(error)


def _wait(exchange: _Exchange, process: BaseProcess, limits: ProcessLimits) -> Failure | None:
    """Block until the exchange finishes. Returns the failure if a limit was hit first."""
    start = time.monotonic()
    next_watchdog = start + _WATCHDOG_SECONDS
    while exchange.is_alive():
        exchange.join(_POLL_SECONDS)
        now = time.monotonic()
        if not process.is_alive():
            exchange.join(_EXIT_GRACE_SECONDS)  # a result may still be in the pipe
            return None
        if now - start >= limits.timeout_s:
            return Failure(
                FailureKind.TIMEOUT, f"still running after {limits.timeout_s} seconds; stopped"
            )
        if limits.memory_mb is not None and sys.platform == "darwin" and now >= next_watchdog:
            next_watchdog = now + _WATCHDOG_SECONDS
            used = _resident_mebibytes(process.pid)
            if used is not None and used > limits.memory_mb:
                return Failure(
                    FailureKind.MEMORY_LIMIT,
                    f"using {used} MiB, over the limit of {limits.memory_mb} MiB; stopped",
                )
    return None


def _resident_mebibytes(pid: int | None) -> int | None:
    """Resident memory of ``pid`` on macOS, or ``None`` if it cannot be read."""
    try:
        output = subprocess.run(  # noqa: S603 - fixed absolute path and an integer argument
            ["/bin/ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        ).stdout
        return int(output.strip()) // 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _interpret(exchange: _Exchange, process: BaseProcess) -> IsolatedResult:
    if exchange.oversized:
        return Failure(FailureKind.INVALID_RESULT, "result is over the size limit")
    if exchange.reply is not None:
        try:
            reply = plaindata.decode(exchange.reply)
            if not (isinstance(reply, tuple) and len(reply) == 2):
                raise PlainDataError("expected a status and a value")
            status, value = reply
            if status == "ok":
                return Ok(value)
            return Failure(FailureKind(str(status)), str(value)[:_MAX_DETAIL_CHARS])
        except (ValueError, RecursionError, MemoryError) as error:  # PlainDataError is a ValueError
            return Failure(FailureKind.INVALID_RESULT, f"result is not plain data: {error}")
    process.join(_TERMINATE_GRACE_SECONDS)
    failure = _describe_exit(process.exitcode)
    if exchange.is_alive():
        return Failure(failure.kind, f"{failure.detail}; the pipe was still open")
    if exchange.pipe_error:
        return Failure(failure.kind, f"{failure.detail}; pipe error: {exchange.pipe_error}")
    return failure


def _signal_name(number: int) -> str:
    """Real-time signals, for example, have no ``signal.Signals`` member."""
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"signal {number}"


def _describe_exit(code: int | None) -> Failure:
    xcpu = getattr(signal, "SIGXCPU", None)
    if xcpu is not None and code == -xcpu:
        return Failure(FailureKind.CPU_LIMIT, "stopped by the CPU time limit")
    if code is not None and code < 0:
        return Failure(FailureKind.CRASHED, f"killed by {_signal_name(-code)}")
    if code is not None and sys.platform == "win32" and code >= 0xC0000000:
        return Failure(FailureKind.CRASHED, f"crashed with status {code:#x}")
    if code == _SETUP_FAILED_EXIT_CODE:
        return Failure(FailureKind.CRASHED, "the child could not apply its limits; it did no work")
    return Failure(FailureKind.CRASHED, f"exited with code {code} without returning a result")


def _stop(process: BaseProcess) -> None:
    """Make sure the child and everything it started are gone."""
    if process.pid is None:
        return  # never started
    process.terminate()
    process.join(_TERMINATE_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_KILL_WAIT_SECONDS)  # bounded: never hang the parent on a stuck child
    if sys.platform != "win32":  # the child leads its own process group; see _harden
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with _child_table_lock, contextlib.suppress(ValueError):  # alive after SIGKILL: OS reaps it
        process.close()


# ------------------------------------------------------------------- Windows containment

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    class _BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class _IoCounters(ctypes.Structure):
        _fields_ = tuple((name, ctypes.c_ulonglong) for name in ("a", "b", "c", "d", "e", "f"))

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    _EXTENDED_LIMIT_INFORMATION = 9
    _LIMIT_PROCESS_MEMORY = 0x100
    _LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    def _contain(process: BaseProcess, limits: ProcessLimits) -> object | None:
        """Windows limits are applied from outside. A Job Object also kills descendants when
        the handle closes, including when the parent dies."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _LIMIT_KILL_ON_JOB_CLOSE
        if limits.memory_mb is not None:
            info.BasicLimitInformation.LimitFlags |= _LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = limits.memory_mb * _MEBIBYTE
        applied = _kernel32.SetInformationJobObject(
            job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ) and _kernel32.AssignProcessToJobObject(job, process.sentinel)
        if not applied:
            error = ctypes.WinError(ctypes.get_last_error())
            _kernel32.CloseHandle(job)
            raise error
        return job

    def _release(job: object | None) -> None:
        if job is not None:
            _kernel32.CloseHandle(job)

else:
    # Reads one byte. End of file means the parent died, so kill what it left behind. The
    # watcher is a separate program because the worker may be stuck in native code, where it
    # could not run a signal handler. Standard library only, and -I keeps it isolated.
    _WATCHER_SCRIPT = """
import os, signal, sys
pid = int(sys.argv[1])
if not os.read(0, 1):
    for kill in (os.killpg, os.kill):
        try:
            kill(pid, signal.SIGKILL)
        except OSError:
            pass
"""

    def _contain(process: BaseProcess, limits: ProcessLimits) -> object | None:
        """Start the watcher that cleans up if this process dies without stopping the child.
        Resource limits are applied by the child itself; see ``_harden``."""
        return subprocess.Popen(  # noqa: S603 - our own interpreter and a fixed script
            [sys.executable, "-I", "-S", "-c", _WATCHER_SCRIPT, str(process.pid)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # a signal to our process group must not reach it
        )

    def _release(job: object | None) -> None:
        """Tell the watcher all is well, and reap it. The child has already been stopped."""
        if not isinstance(job, subprocess.Popen):
            return
        with contextlib.suppress(OSError):
            job.stdin.write(b"x")  # type: ignore[union-attr]
        with contextlib.suppress(OSError):
            job.stdin.close()  # type: ignore[union-attr]
        try:
            job.wait(_WATCHER_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            job.kill()
            job.wait()


# ------------------------------------------------------------------------- child process


def _child_main(
    connection: Connection, func: Callable[[Any], Any], limits: ProcessLimits, workdir: str
) -> None:
    try:
        _harden(limits, workdir)
    except (OSError, ValueError):
        sys.exit(_SETUP_FAILED_EXIT_CODE)  # a limit that cannot be set must not run the work
    try:
        message = _reply_for(connection, func)
    except MemoryError:
        message = _memory_reply("ran out of memory sending the result")
    connection.send_bytes(message)
    connection.close()


def _reply_for(connection: Connection, func: Callable[[Any], Any]) -> bytes:
    try:
        # Sent by the trusted parent. Replies to it use plaindata, never pickle.
        payload = pickle.loads(connection.recv_bytes())  # noqa: S301  # nosec B301
    except MemoryError:
        return _memory_reply("ran out of memory receiving the payload")
    try:
        reply: tuple[str, object] = ("ok", func(payload))
    except MemoryError:
        return _memory_reply("ran out of memory under the limit")
    except Exception as error:  # noqa: BLE001 - reported to the parent, which decides
        reply = (FailureKind.RAISED.value, f"{type(error).__name__}: {error}")
    try:
        return plaindata.encode(reply)
    except (PlainDataError, RecursionError) as error:
        return plaindata.encode((FailureKind.INVALID_RESULT.value, f"not plain data: {error}"))


def _memory_reply(detail: str) -> bytes:
    return plaindata.encode((FailureKind.MEMORY_LIMIT.value, detail))


def _harden(limits: ProcessLimits, workdir: str) -> None:
    """Drop what the child should not inherit. Runs before it receives any work."""
    kept = {k: os.environ[k] for k in _WINDOWS_ENVIRONMENT if k in os.environ}
    os.environ.clear()
    os.environ.update(
        kept, PATH=os.defpath, HOME=workdir, TMPDIR=workdir, TEMP=workdir, TMP=workdir
    )
    os.chdir(workdir)
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # interruption is the parent's decision
    if sys.platform == "win32":
        return
    os.setsid()  # own process group, so the parent can kill what this child starts
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))  # no core dumps of evidence in memory
    if limits.cpu_s is not None:
        soft = math.ceil(limits.cpu_s)
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        ceiling = soft + 1 if hard == resource.RLIM_INFINITY else min(soft + 1, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (min(soft, ceiling), ceiling))
    if limits.memory_mb is not None and sys.platform != "darwin":  # macOS ignores RLIMIT_AS
        size = limits.memory_mb * _MEBIBYTE
        resource.setrlimit(resource.RLIMIT_AS, (size, size))

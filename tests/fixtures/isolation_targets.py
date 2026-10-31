"""Callables that misbehave in a child process. Importable, so they can be spawned."""

import faulthandler
import os
import signal
import subprocess
import sys
import time
from typing import Any


def echo(payload: Any) -> Any:
    return payload


def double(payload: int) -> int:
    return payload * 2


def raise_value_error(_: object) -> None:
    raise ValueError("deliberate failure")


def raise_memory_error(_: object) -> None:
    raise MemoryError


def segfault(_: object) -> None:
    """A real crash. ctypes would turn an access violation into OSError on Windows."""
    faulthandler._sigsegv()  # type: ignore[attr-defined]


def raise_realtime_signal(_: object) -> None:
    signal.raise_signal(signal.SIGRTMIN + 1)  # type: ignore[attr-defined]


def killed_abruptly(_: object) -> None:
    """Dies from a signal like a crash does, without triggering a crash report."""
    os.kill(os.getpid(), getattr(signal, "SIGKILL", signal.SIGTERM))


def hang(_: object) -> None:
    time.sleep(3600)


def spin(_: object) -> None:
    while True:
        pass


def allocate_forever(_: object) -> None:
    chunks = []
    while True:
        chunks.append(bytearray(b"x" * (16 << 20)))  # touched, so it counts as resident


def large_result(size: int) -> bytes:
    return b"x" * size


def unpicklable_result(_: object) -> object:
    return lambda: None


def class_result(_: object) -> object:
    return ValueError("a class crossing the boundary")


def exit_cleanly_without_result(_: object) -> None:
    os._exit(0)


def exit_with_code(code: int) -> None:
    os._exit(code)


def ignore_sigterm_and_hang(_: object) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(3600)


def environment_and_directory(_: object) -> dict[str, str | list[str]]:
    return {"cwd": os.getcwd(), "keys": sorted(os.environ)}


def start_grandchild_and_hang(pid_file: str) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(pid_file, "w") as handle:
        handle.write(str(child.pid))
    time.sleep(3600)


def memory_bomb_pickle_result(_: object) -> None:
    """A tiny pickle whose memo index would make an unpickler allocate gigabytes."""
    import gc
    from multiprocessing.connection import _ConnectionBase

    bomb = b"\x80\x05N" + b"r\xff\xff\xff\x7f" + b"."  # LONG_BINPUT at index 2**31 - 1
    for candidate in gc.get_objects():
        if isinstance(candidate, _ConnectionBase) and not candidate.closed:
            candidate.send_bytes(bomb)
    os._exit(0)


def forged_pickle_result(marker: str) -> None:
    """A compromised child writes bytes directly to the pipe, skipping the normal reply."""
    import gc
    from multiprocessing.connection import _ConnectionBase

    malicious = (
        b"cos\nsystem\n(S'touch " + marker.encode() + b"'\ntR."  # os.system("touch marker")
    )
    for candidate in gc.get_objects():
        if isinstance(candidate, _ConnectionBase) and not candidate.closed:
            candidate.send_bytes(malicious)
    os._exit(0)


def half_message_then_hang(_: object) -> None:
    """Claims a long message, sends part of it, then hangs with the pipe open."""
    import gc
    import struct
    from multiprocessing.connection import _ConnectionBase

    for candidate in gc.get_objects():
        if isinstance(candidate, _ConnectionBase) and not candidate.closed:
            os.write(candidate.fileno(), struct.pack("!i", 1 << 20) + b"partial")
    time.sleep(3600)

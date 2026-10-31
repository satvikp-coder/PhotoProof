"""Conservative local process identity for stale-run recovery, never remote PID probing."""

import ctypes
import os
import socket
import subprocess
import sys
from collections.abc import Mapping
from ctypes import wintypes
from pathlib import Path
from typing import Literal

ProcessState = Literal["alive", "dead", "unknown"]


def process_token(pid: int) -> str | None:
    """Creation identity, or None when permission/platform limitations prevent inspection."""
    try:
        if sys.platform == "win32":
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.GetProcessTimes.argtypes = [
                wintypes.HANDLE,
                *([ctypes.POINTER(wintypes.FILETIME)] * 4),
            ]
            api.GetProcessTimes.restype = wintypes.BOOL
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle.restype = wintypes.BOOL
            handle = api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                if ctypes.get_last_error() == 87:  # nonexistent PID
                    raise ProcessLookupError(pid)
                return None
            try:
                created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
                if api.GetProcessTimes(
                    handle,
                    ctypes.byref(created),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    if exited.dwHighDateTime or exited.dwLowDateTime:
                        raise ProcessLookupError(pid)
                    return f"windows:{created.dwHighDateTime}:{created.dwLowDateTime}"
                return None
            finally:
                api.CloseHandle(handle)
        if sys.platform == "linux":
            try:
                fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            except FileNotFoundError as error:
                raise ProcessLookupError(pid) from error
            if fields[0] == "Z":
                raise ProcessLookupError(pid)
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return f"linux:{boot}:{fields[19]}"  # stat field 22: start ticks since boot
        os.kill(pid, 0)
        if sys.platform == "darwin":
            result = subprocess.run(  # noqa: S603 - fixed binary and integer PID only
                ["/bin/ps", "-p", str(pid), "-o", "lstart="],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
                env={"LC_ALL": "C"},
            )
            return (
                f"darwin:{result.stdout.strip()}"
                if result.returncode == 0 and result.stdout.strip()
                else None
            )
    except ProcessLookupError:
        raise
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None  # ambiguity is preserved, never treated as death
    return None


def owner_state(run: Mapping[str, object]) -> ProcessState:
    """Only demonstrably dead local owners are eligible for automated recovery."""
    if run.get("hostname") != socket.gethostname():
        return "unknown"
    pid = run.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return "unknown"
    try:
        token = process_token(pid)
    except ProcessLookupError:
        return "dead"
    recorded = run.get("process_token")
    if token is None or not isinstance(recorded, str):
        return "unknown"
    return "alive" if token == recorded else "dead"

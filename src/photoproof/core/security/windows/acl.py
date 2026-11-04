"""Windows ACL helpers for owner-only runtime directory permissions.

Uses ``icacls`` via subprocess (argument list, never shell=True) to apply and
inspect access control lists on Windows, where POSIX modes (0700, 0400) have
no effect.

Documented per issue #80: icacls is used because ctypes Win32 security APIs
are significantly more complex and error-prone for this use case.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import shutil
import subprocess

_WINDOWS = os.name == "nt"


def _icacls_path() -> str | None:
    """Return the full path to the ``icacls`` executable on Windows.

    Resolves ``icacls`` through ``shutil.which`` so Bandit's B607 check
    (partial executable path) is not triggered. Falls back to the standard
    Windows system directory when not found on PATH.
    """
    if not _WINDOWS:
        return None
    found = shutil.which("icacls")
    if found:
        return found
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    fallback = os.path.join(system_root, "System32", "icacls.exe")
    return fallback if os.path.exists(fallback) else None


def is_windows() -> bool:
    """Return True when running on Windows."""
    return _WINDOWS


def get_current_username() -> str:
    """Return the current Windows username."""
    return os.environ.get("USERNAME", "")


def apply_owner_only_acl(directory: str) -> None:
    """Apply owner-only ACLs to ``directory`` on Windows.

    Removes inheritance and grants the current user full control with
    object and container inheritance. On non-Windows systems this is a
    no-op (POSIX modes are handled separately).

    Raises ``subprocess.CalledProcessError`` if icacls fails.
    """
    if not _WINDOWS:
        return

    username = get_current_username()
    if not username:
        return

    icacls = _icacls_path()
    if not icacls:
        return

    subprocess.run(  # noqa: S603
        [
            icacls,
            directory,
            "/inheritance:r",
            "/grant:r",
            f"{username}:(OI)(CI)F",
        ],
        check=True,
        capture_output=True,
    )


def check_acl_for_other_principals(directory: str) -> bool:
    """Return True if ``directory`` has ACL entries granting access to
    principals other than the owner, SYSTEM, or Administrators on Windows.

    On non-Windows systems this always returns False (POSIX modes are
    handled separately).

    Uses ``icacls`` output parsing; documented per issue #80.
    """
    if not _WINDOWS:
        return False

    username = get_current_username()
    if not username:
        return False

    icacls = _icacls_path()
    if not icacls:
        return False

    try:
        result = subprocess.run(  # noqa: S603
            [
                icacls,
                directory,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

    output = result.stdout
    # icacls output lists ACEs like:
    #   USERNAME:(OI)(CI)F
    #   NT AUTHORITY\SYSTEM:(OI)(CI)F
    #   BUILTIN\Administrators:(OI)(CI)F
    # We flag any ACE that is not for the current user, SYSTEM, or Administrators.
    for current_line in output.splitlines():
        stripped_line = current_line.strip()
        if ":" not in stripped_line:
            continue
        # Skip header lines like "Successfully processed 1 files"
        if "successfully processed" in stripped_line.lower():
            continue
        # Extract the principal name before the first colon
        principal = stripped_line.split(":")[0].strip()
        if principal.lower() in (
            username.lower(),
            "nt authority\\system",
            "builtin\\administrators",
            "everyone",
        ):
            # Everyone is flagged as insecure (grants access to all users)
            if principal.lower() == "everyone":
                return True
            continue
        # Any other principal = insecure
        return True

    return False


def make_file_read_only(path: str) -> None:
    """Mark a file as read-only on Windows using FILE_ATTRIBUTE_READONLY.

    On POSIX this is a no-op (file modes are handled separately).
    """
    if not _WINDOWS:
        return

    try:
        kernel32 = ctypes.windll.kernel32
        FILE_ATTRIBUTE_READONLY = 0x01
        kernel32.SetFileAttributesW(path, FILE_ATTRIBUTE_READONLY)
    except (OSError, AttributeError):
        pass

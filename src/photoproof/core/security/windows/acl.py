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
import re
import shutil
import subprocess
import time

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


#: Re-try delays for icacls. On a busy machine (an indexer or scanner touching the
#: directory right after it is created) icacls can fail transiently; a few re-tries make
#: ACL application and inspection reliable without hiding a persistent failure.
_RETRY_DELAYS = (0.0, 0.2, 0.5)


#: icacls exits 0 even when it could not touch every file; its summary line says so.
_FAILED_PROCESSING = re.compile(r"Failed processing\s+(\d+)")


def _run_icacls(icacls: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run ``icacls`` with arguments, retrying a transient failure.

    icacls reports a failure both with a nonzero exit and, misleadingly, with exit 0 and a
    ``Failed processing N files`` summary, so the output is examined too. Raises the last
    ``subprocess.CalledProcessError``/``OSError`` when every attempt fails.
    """
    last_error: Exception = RuntimeError("icacls was never attempted")
    for delay in _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            completed = subprocess.run(  # noqa: S603
                [icacls, *arguments], check=True, capture_output=True, text=True
            )
        except (subprocess.CalledProcessError, OSError) as error:
            last_error = error
            continue
        summary = _FAILED_PROCESSING.search(completed.stdout)
        if summary and int(summary.group(1)) > 0:
            last_error = subprocess.CalledProcessError(
                completed.returncode, completed.args, completed.stdout, completed.stderr
            )
            continue
        return completed
    raise last_error


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

    _run_icacls(icacls, directory, "/inheritance:r", "/grant:r", f"{username}:(OI)(CI)F")


#: Principals that are never "another user", matched case-insensitively by name and by
#: their language-independent SID (icacls sometimes prints the SID instead of the name).
_SAFE_PRINCIPALS = frozenset(
    {
        "nt authority\\system",
        "builtin\\administrators",
        "owner rights",
        "s-1-5-18",  # NT AUTHORITY\SYSTEM
        "s-1-5-32-544",  # BUILTIN\Administrators
        "s-1-3-4",  # OWNER RIGHTS
    }
)

#: "Everyone" grants access to every account and is always reported.
_EVERYONE = frozenset({"everyone", "s-1-1-0"})


def other_principals(directory: str) -> tuple[str, ...]:
    """Principals in ``directory``'s ACL other than the current account or a system one.

    The current account may appear as ``user``, ``MACHINE\\user`` or ``DOMAIN\\user``;
    SYSTEM, Administrators and OWNER RIGHTS are recognised by name and by SID, because
    ``icacls`` prints one or the other depending on the system.

    Returns ``("<unverifiable>",)`` when the ACL cannot be inspected at all, so a directory
    is never assumed owner-only on the strength of a failed check. On non-Windows systems
    this is always empty (POSIX modes are handled separately).

    Parses ``icacls`` output; documented per issue #80.
    """
    if not _WINDOWS:
        return ()

    username = get_current_username()
    icacls = _icacls_path()
    if not username or not icacls:
        return ("<unverifiable>",)

    try:
        result = _run_icacls(icacls, directory)
    except (subprocess.CalledProcessError, OSError):
        return ("<unverifiable>",)

    # icacls output lists ACEs like:
    #   C:\path\to\dir MACHINE\user:(OI)(CI)(F)
    #                  NT AUTHORITY\SYSTEM:(OI)(CI)(F)
    #                  BUILTIN\Administrators:(OI)(CI)(F)
    #                  OWNER RIGHTS:(OI)(CI)(F)
    # The first ACE line begins with the directory (whose drive letter also contains a
    # colon), so an ACE is recognised by its ``:(permissions)`` suffix.
    allowed = set(_SAFE_PRINCIPALS)
    allowed.add(username.lower())
    for prefix in (os.environ.get("USERDOMAIN", ""), os.environ.get("COMPUTERNAME", "")):
        if prefix:
            allowed.add(f"{prefix.lower()}\\{username.lower()}")
    lowered_directory = directory.lower()
    offenders: list[str] = []
    for current_line in result.stdout.splitlines():
        stripped_line = current_line.strip()
        ace_at = stripped_line.find(":(")
        if ace_at < 0:
            continue  # header lines and footers carry no ACE
        head = stripped_line[:ace_at].strip().lower()
        principal = head
        if head.startswith(lowered_directory):
            principal = head[len(lowered_directory) :].strip()
        candidates = {principal}
        if " " in principal:  # a name that contains a space, e.g. OWNER RIGHTS
            candidates.add(principal.rsplit(None, 1)[-1])
        if not principal:
            continue
        if candidates & _EVERYONE or not candidates & allowed:
            offenders.append(principal)
    return tuple(offenders)


def check_acl_for_other_principals(directory: str) -> bool:
    """Return True if ``directory`` grants access to a principal other than the owner,
    SYSTEM or Administrators on Windows.

    On non-Windows systems this always returns False (POSIX modes are handled
    separately). An ACL that cannot be inspected is reported, never assumed owner-only.
    """
    return bool(other_principals(directory))


def make_file_read_only(path: str) -> None:
    """Mark a file as read-only on Windows using FILE_ATTRIBUTE_READONLY.

    On POSIX this is a no-op (file modes are handled separately).
    """
    if not _WINDOWS:
        return

    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # Windows only
        FILE_ATTRIBUTE_READONLY = 0x01
        kernel32.SetFileAttributesW(path, FILE_ATTRIBUTE_READONLY)
    except (OSError, AttributeError):
        pass


def clear_file_read_only(path: str) -> None:
    """Remove the read-only attribute from a file on Windows.

    Used when a staged file was marked read-only for publication but will not be
    published after all: Windows refuses to delete a read-only file. On POSIX this
    is a no-op.
    """
    if not _WINDOWS:
        return

    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # Windows only
        invalid_attributes = 0xFFFFFFFF
        readonly = 0x01
        attributes = kernel32.GetFileAttributesW(path)
        if attributes != invalid_attributes and attributes & readonly:
            kernel32.SetFileAttributesW(path, attributes & ~readonly)
    except (OSError, AttributeError):
        pass

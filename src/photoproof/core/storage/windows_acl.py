"""SID-based Windows runtime permissions. No shell, account-name lookup or elevation.

Only current-token user, LocalSystem and built-in Administrators are approved.
Directory mutation handles use MAXIMUM_ALLOWED to prevent SetSecurityInfo propagating ACL
changes to children: callers enumerate only the managed runtime paths explicitly.
"""

import ctypes
import os
import re
import struct
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from photoproof.contracts.exceptions import StorageError

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
FULL_CONTROL = 0x1F01FF
FILE_READ = 0x120089
READONLY = 0x1
REPARSE_POINT = 0x400
PERSISTENT_ACLS = 0x8
MAXIMUM_ALLOWED = 0x02000000
OWNER_AND_DACL = 0x5
PROTECTED_DACL = 0x80000004
READ_CONTROL = 0x20000
WRITE_DAC = 0x40000
FILE_READ_ATTRIBUTES = 0x80
FILE_WRITE_ATTRIBUTES = 0x100
DIRECTORY_ATTRIBUTE = 0x10
NORMAL_ATTRIBUTE = 0x80
SHARE_READ_WRITE = 0x3
SHARE_READ_WRITE_DELETE = 0x7
OPEN_EXISTING = 3
BACKUP_SEMANTICS = 0x02000000
OPEN_REPARSE_POINT = 0x00200000
DRIVE_REMOTE = 4
ERROR_ALREADY_EXISTS = 183
INHERIT_TO_CHILDREN = 0x3
INHERITED_ACE = 0x10
TOKEN_QUERY = 0x8
TOKEN_USER = 1
SDDL_REVISION = 1
INVALID_ATTRIBUTES = 0xFFFFFFFF
DACL_PROTECTED_CONTROL = 0x1000
SE_FILE_OBJECT = 1
FILE_BASIC_INFO = 0


class UnsupportedACL(StorageError):
    """The configured filesystem cannot provide the local ACL guarantee."""


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("descriptor", ctypes.c_void_p),
        ("inherit", ctypes.c_int),
    ]


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", ctypes.c_uint32),
        ("creation", ctypes.c_uint32 * 2),
        ("access", ctypes.c_uint32 * 2),
        ("write", ctypes.c_uint32 * 2),
        ("volume", ctypes.c_uint32),
        ("size_high", ctypes.c_uint32),
        ("size_low", ctypes.c_uint32),
        ("links", ctypes.c_uint32),
        ("index_high", ctypes.c_uint32),
        ("index_low", ctypes.c_uint32),
    ]


class _BasicInfo(ctypes.Structure):
    _fields_ = [("times", ctypes.c_int64 * 4), ("attributes", ctypes.c_uint32)]


class _API:
    def __init__(self) -> None:
        self.kernel = _load_library("kernel32")
        self.security = _load_library("advapi32")
        pointer, integer, dword, text = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
        )
        # Explicit pointer-width signatures are required on 64-bit Windows.
        signatures: tuple[tuple[ctypes.CDLL, str, object, list[object]], ...] = (
            (self.kernel, "GetCurrentProcess", pointer, []),
            (self.kernel, "CloseHandle", integer, [pointer]),
            (self.kernel, "LocalFree", pointer, [pointer]),
            (
                self.kernel,
                "CreateFileW",
                pointer,
                [text, dword, dword, pointer, dword, dword, pointer],
            ),
            (self.kernel, "CreateDirectoryW", integer, [text, pointer]),
            (self.kernel, "GetFileAttributesW", dword, [text]),
            (self.kernel, "GetFileInformationByHandle", integer, [pointer, pointer]),
            (
                self.kernel,
                "SetFileInformationByHandle",
                integer,
                [pointer, integer, pointer, dword],
            ),
            (self.kernel, "GetFinalPathNameByHandleW", dword, [pointer, text, dword, dword]),
            (self.kernel, "GetLongPathNameW", dword, [text, text, dword]),
            (self.kernel, "GetVolumePathNameW", integer, [text, text, dword]),
            (self.kernel, "GetDriveTypeW", dword, [text]),
            (
                self.kernel,
                "GetVolumeInformationW",
                integer,
                [text, text, dword, pointer, pointer, pointer, text, dword],
            ),
            (self.security, "OpenProcessToken", integer, [pointer, dword, pointer]),
            (
                self.security,
                "GetTokenInformation",
                integer,
                [pointer, integer, pointer, dword, pointer],
            ),
            (self.security, "ConvertSidToStringSidW", integer, [pointer, pointer]),
            (
                self.security,
                "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                integer,
                [text, dword, pointer, pointer],
            ),
            (
                self.security,
                "GetSecurityInfo",
                dword,
                [pointer, integer, dword, pointer, pointer, pointer, pointer, pointer],
            ),
            (
                self.security,
                "SetSecurityInfo",
                dword,
                [pointer, integer, dword, pointer, pointer, pointer, pointer],
            ),
            (
                self.security,
                "GetSecurityDescriptorDacl",
                integer,
                [pointer, pointer, pointer, pointer],
            ),
            (self.security, "GetSecurityDescriptorControl", integer, [pointer, pointer, pointer]),
            (self.security, "GetAce", integer, [pointer, dword, pointer]),
        )
        for library, name, result, arguments in signatures:
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments


def _load_library(name: str) -> ctypes.CDLL:
    if sys.platform != "win32":
        raise UnsupportedACL("Windows security libraries require Windows")
    return ctypes.WinDLL(name, use_last_error=True)


def _last_error() -> int:
    if sys.platform != "win32":
        raise UnsupportedACL("Windows error reporting requires Windows")
    return ctypes.get_last_error()


@lru_cache(maxsize=1)
def _api() -> _API:
    if os.name != "nt":
        raise UnsupportedACL("Windows ACL APIs require Windows")
    return _API()


def _error(operation: str, path: Path | None = None, *, code: int | None = None) -> StorageError:
    if sys.platform != "win32":
        return StorageError("Windows security APIs are unavailable on this platform")
    number = _last_error() if code is None else code
    return StorageError(
        f"{path or 'Windows token'}: {operation} failed (WinError {number}: "
        f"{ctypes.FormatError(number).strip()}). Use a local ACL-capable volume and an "
        "account permitted to manage this runtime; no privilege escalation was attempted."
    )


def _sid_text(sid: int | ctypes.c_void_p) -> str:
    output = ctypes.c_void_p()
    api = _api()
    if not api.security.ConvertSidToStringSidW(sid, ctypes.byref(output)):
        raise _error("ConvertSidToStringSidW")
    try:
        return ctypes.wstring_at(output)
    finally:
        api.kernel.LocalFree(output)


@lru_cache(maxsize=1)
def current_user_sid() -> str:
    """Read TokenUser, not USERNAME or the default token owner (which may be a group)."""
    api, token, size = _api(), ctypes.c_void_p(), ctypes.c_uint32()
    if not api.security.OpenProcessToken(
        api.kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise _error("OpenProcessToken")
    try:
        api.security.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
        if not size.value:
            raise _error("GetTokenInformation size")
        buffer = ctypes.create_string_buffer(size.value)
        if not api.security.GetTokenInformation(
            token, TOKEN_USER, buffer, size, ctypes.byref(size)
        ):
            raise _error("GetTokenInformation TokenUser")
        sid = ctypes.c_void_p.from_buffer(buffer).value
        if sid is None:
            raise StorageError("Windows token returned no user SID")
        return _sid_text(sid)
    finally:
        api.kernel.CloseHandle(token)


def descriptor_sddl(user_sid: str, *, directory: bool, readonly: bool = False) -> str:
    """Build a canonical allowlist without localized names; domain SIDs work unchanged."""
    if not re.fullmatch(r"S-\d+(?:-\d+)+", user_sid):
        raise StorageError("invalid user SID for Windows runtime ACL")
    principals = dict.fromkeys((user_sid, SYSTEM_SID, ADMINISTRATORS_SID))
    flags, rights = "OICI" if directory else "", "FR" if readonly else "FA"
    return f"O:{user_sid}D:P" + "".join(f"(A;{flags};{rights};;;{sid})" for sid in principals)


@contextmanager
def _descriptor(sddl: str) -> Iterator[ctypes.c_void_p]:
    descriptor = ctypes.c_void_p()
    api = _api()
    if not api.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION, ctypes.byref(descriptor), None
    ):
        raise _error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    try:
        yield descriptor
    finally:
        api.kernel.LocalFree(descriptor)


def require_supported_volume(path: Path) -> None:
    """Refuse UNC/mapped network paths and volumes without persistent ACLs."""
    absolute = os.path.abspath(path)
    if "\x00" in absolute:
        raise StorageError("refusing a NUL-containing Windows runtime path")
    if absolute.startswith("\\\\"):
        raise UnsupportedACL(f"{path}: network/UNC runtime ACLs are unverified; use a local volume")
    api, volume = _api(), ctypes.create_unicode_buffer(32768)
    if not api.kernel.GetVolumePathNameW(absolute, volume, len(volume)):
        raise _error("GetVolumePathNameW", path)
    if (
        api.kernel.GetDriveTypeW(volume.value) == DRIVE_REMOTE
    ):  # DRIVE_REMOTE, including mapped shares
        raise UnsupportedACL(
            f"{path}: mapped network runtime ACLs are unverified; use a local volume"
        )
    flags = ctypes.c_uint32()
    if not api.kernel.GetVolumeInformationW(
        volume.value, None, 0, None, None, ctypes.byref(flags), None, 0
    ):
        raise _error("GetVolumeInformationW", path)
    if not flags.value & PERSISTENT_ACLS:
        raise UnsupportedACL(f"{path}: filesystem lacks persistent ACLs; use local NTFS/ReFS")


def _reject_reparse_ancestors(path: Path, *, boundary: Path | None = None) -> None:
    api = _api()
    absolute = Path(os.path.abspath(path))
    if boundary is not None and absolute != boundary and boundary not in absolute.parents:
        raise StorageError(f"{path}: ACL scan path is outside its trusted runtime")
    for component in (absolute, *absolute.parents):
        attributes = api.kernel.GetFileAttributesW(str(component))
        if attributes != INVALID_ATTRIBUTES and attributes & REPARSE_POINT:
            raise StorageError(f"{component}: refusing ACL operations through a reparse point")
        if component == boundary:
            break


@contextmanager
def _handle(
    path: Path,
    *,
    access: int = READ_CONTROL | FILE_READ_ATTRIBUTES,
    share: int = SHARE_READ_WRITE_DELETE,
    boundary: Path | None = None,
) -> Iterator[int]:
    _reject_reparse_ancestors(path, boundary=boundary)
    api = _api()
    handle = api.kernel.CreateFileW(
        os.path.abspath(path),
        access,
        share,
        None,
        OPEN_EXISTING,
        BACKUP_SEMANTICS | OPEN_REPARSE_POINT,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise _error("CreateFileW security handle", path)
    try:
        info = _file_info(handle, path)
        if info.attributes & REPARSE_POINT or info.links > 1:
            raise StorageError(f"{path}: refusing ACL operations on reparse points or hard links")
        final = ctypes.create_unicode_buffer(32768)
        length = api.kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
        if not length or length >= len(final):
            raise _error("GetFinalPathNameByHandleW", path)
        # Hosted Windows runners may use an 8.3 TEMP path (e.g. RUNNER~1).
        # Expand names without accepting a junction/symlink redirection.
        expected = ctypes.create_unicode_buffer(32768)
        length = api.kernel.GetLongPathNameW(os.path.abspath(path), expected, len(expected))
        if not length or length >= len(expected):
            raise _error("GetLongPathNameW", path)
        if os.path.normcase(final.value.removeprefix("\\\\?\\")) != os.path.normcase(
            expected.value.removeprefix("\\\\?\\")
        ):
            raise StorageError(f"{path}: security handle resolved to a different path")
        yield handle
    finally:
        api.kernel.CloseHandle(handle)


def _file_info(handle: int, path: Path) -> _FileInfo:
    info = _FileInfo()
    if not _api().kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise _error("GetFileInformationByHandle", path)
    return info


@dataclass(frozen=True)
class ACE:
    sid: str
    mask: int
    flags: int
    kind: int = 0


@dataclass(frozen=True)
class ACLSnapshot:
    owner: str
    protected: bool
    aces: tuple[ACE, ...]
    attributes: int
    null_dacl: bool = False


def _snapshot(handle: int, path: Path) -> ACLSnapshot:
    api = _api()
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    result = api.security.GetSecurityInfo(
        handle,
        1,
        OWNER_AND_DACL,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result:
        raise _error("GetSecurityInfo", path, code=result)
    try:
        control, revision = ctypes.c_uint16(), ctypes.c_uint32()
        if not api.security.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise _error("GetSecurityDescriptorControl", path)
        aces = []
        if dacl.value is None:
            return ACLSnapshot(
                _sid_text(owner),
                bool(control.value & DACL_PROTECTED_CONTROL),
                (),
                _file_info(handle, path).attributes,
                null_dacl=True,
            )
        count = ctypes.c_uint16.from_address(dacl.value + 4).value
        for index in range(count):
            pointer = ctypes.c_void_p()
            if not api.security.GetAce(dacl, index, ctypes.byref(pointer)) or pointer.value is None:
                raise _error("GetAce", path)
            kind, flags, _size = struct.unpack("<BBH", ctypes.string_at(pointer, 4))
            if kind not in {0, 1}:
                aces.append(ACE("unsupported ACE", 0, flags, kind))
                continue
            mask = ctypes.c_uint32.from_address(pointer.value + 4).value
            aces.append(ACE(_sid_text(pointer.value + 8), mask, flags, kind))
        return ACLSnapshot(
            _sid_text(owner),
            bool(control.value & DACL_PROTECTED_CONTROL),
            tuple(aces),
            _file_info(handle, path).attributes,
        )
    finally:
        api.kernel.LocalFree(descriptor)


def inspect(path: Path, *, _scan_root: Path | None = None) -> ACLSnapshot:
    """Inspect actual native DACL/owner/attributes; unsupported reads never mean OK."""
    if _scan_root is None:
        require_supported_volume(path)
    # Internal scans check the volume once and reject all descendant reparse points.
    with _handle(path, boundary=_scan_root) as handle:
        return _snapshot(handle, path)


def problems(
    snapshot: ACLSnapshot, *, directory: bool, readonly: bool = False, protected: bool = False
) -> tuple[str, ...]:
    """Conservatively require the canonical allowlist, not a partial effective-access guess."""
    allowed = {current_user_sid(), SYSTEM_SID, ADMINISTRATORS_SID}
    issues = []
    if snapshot.null_dacl:
        issues.append("NULL DACL grants unrestricted access")
    if snapshot.owner not in allowed:
        issues.append("owned by an unapproved principal")
    if (protected or readonly) and not snapshot.protected:
        issues.append("DACL inheritance is not protected")
    expected = FILE_READ if readonly else FULL_CONTROL
    seen = set()
    for ace in snapshot.aces:
        if (
            ace.kind != 0
            or ace.sid not in allowed
            or ace.mask != expected
            or ace.flags & ~(INHERIT_TO_CHILDREN | INHERITED_ACE)
            or (directory and ace.flags & INHERIT_TO_CHILDREN != INHERIT_TO_CHILDREN)
        ):
            issues.append(
                f"unexpected ACE for {ace.sid} (mask={ace.mask:#x}, flags={ace.flags:#x})"
            )
        seen.add(ace.sid)
    if seen != allowed or len(snapshot.aces) != len(allowed):
        issues.append("canonical user/SYSTEM/Administrators ACEs are missing or duplicated")
    if readonly and not snapshot.attributes & READONLY:
        issues.append("read-only file attribute is absent")
    return tuple(issues)


def _set_acl(handle: int, path: Path, *, directory: bool, readonly: bool = False) -> None:
    before = _snapshot(handle, path)
    if before.owner not in {current_user_sid(), SYSTEM_SID, ADMINISTRATORS_SID}:
        raise StorageError(f"{path}: unapproved owner; arrange an explicit ownership migration")
    with _descriptor(
        descriptor_sddl(current_user_sid(), directory=directory, readonly=readonly)
    ) as descriptor:
        present, defaulted, dacl = ctypes.c_int(), ctypes.c_int(), ctypes.c_void_p()
        if (
            not _api().security.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            )
            or not present.value
            or not dacl.value
        ):
            raise _error("GetSecurityDescriptorDacl", path)
        result = _api().security.SetSecurityInfo(
            handle, SE_FILE_OBJECT, PROTECTED_DACL, None, None, dacl, None
        )
        if result:
            raise _error("SetSecurityInfo", path, code=result)


def ensure_directory(path: Path, *, repair_existing: bool = True, protected: bool = True) -> None:
    """Create with a protected descriptor, or restrict this one existing directory."""
    require_supported_volume(path)
    _reject_reparse_ancestors(path)
    if not path.exists():
        if not path.parent.exists():
            ensure_directory(path.parent, repair_existing=repair_existing)
        with _descriptor(descriptor_sddl(current_user_sid(), directory=True)) as descriptor:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            if (
                not _api().kernel.CreateDirectoryW(os.path.abspath(path), ctypes.byref(attributes))
                and _last_error()
                != ERROR_ALREADY_EXISTS  # already exists: inspect, never assume safe
            ):
                raise _error("CreateDirectoryW with private ACL", path)
    initial = inspect(path)
    if not initial.attributes & DIRECTORY_ATTRIBUTE:
        raise StorageError(f"{path}: runtime directory is not a directory")
    if not problems(initial, directory=True, protected=protected):
        return
    if not repair_existing:
        raise StorageError(f"{path}: insecure runtime ACL; run --doctor and explicit/SAFE repair")
    try:
        with _handle(path, access=MAXIMUM_ALLOWED, share=SHARE_READ_WRITE) as handle:
            _set_acl(handle, path, directory=True)
            issues = problems(_snapshot(handle, path), directory=True, protected=True)
            if issues:
                raise StorageError(f"{path}: directory ACL readback failed: {'; '.join(issues)}")
    except StorageError:
        # Concurrent initialization may have completed the same restriction first.
        # Only a fresh, exact native verification permits success after a denied open.
        if not problems(inspect(path), directory=True, protected=True):
            return
        raise


def restrict_file(path: Path, *, readonly: bool) -> None:
    """Restrict file metadata only; never rewrite bytes, remove data or broaden a DACL."""
    require_supported_volume(path)
    # Do not request data/delete access: SQLite/log handles may be open already.
    # Only an absent DOS attribute needs WRITE_ATTRIBUTES; an already sealed blob
    # can be verified or have a broadened DACL tightened with owner WRITE_DAC alone.
    initial = inspect(path)
    if initial.attributes & DIRECTORY_ATTRIBUTE:
        raise StorageError(f"{path}: expected a file, not a directory")
    if initial.owner not in {current_user_sid(), SYSTEM_SID, ADMINISTRATORS_SID}:
        raise StorageError(f"{path}: unapproved owner; arrange an explicit ownership migration")
    if not problems(initial, directory=False, readonly=readonly, protected=True):
        return
    access = READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES
    if readonly and not initial.attributes & READONLY:
        access |= FILE_WRITE_ATTRIBUTES
    try:
        with _handle(path, access=access, share=SHARE_READ_WRITE) as handle:
            before = _snapshot(handle, path)
            if not problems(before, directory=False, readonly=readonly, protected=True):
                return
            if readonly and not before.attributes & READONLY:
                # The retained handle has original rights; no temporary write grant is made.
                basic = _BasicInfo()
                # FILE_ATTRIBUTE_NORMAL must not be combined with other attributes.
                basic.attributes = (before.attributes & ~NORMAL_ATTRIBUTE) | READONLY
                if not _api().kernel.SetFileInformationByHandle(
                    handle, FILE_BASIC_INFO, ctypes.byref(basic), ctypes.sizeof(basic)
                ):
                    raise _error("SetFileInformationByHandle read-only attribute", path)
            _set_acl(handle, path, directory=False, readonly=readonly)
            issues = problems(
                _snapshot(handle, path), directory=False, readonly=readonly, protected=True
            )
            if issues:
                raise StorageError(f"{path}: file ACL readback failed: {'; '.join(issues)}")
    except StorageError as error:
        # Another publisher can seal this same content address between inspection
        # and opening for metadata changes. Never grant write rights to retry.
        if not problems(inspect(path), directory=False, readonly=readonly, protected=True):
            return
        if (
            readonly
            and not initial.attributes & READONLY
            and all(ace.mask == FILE_READ for ace in initial.aces)
        ):
            raise StorageError(
                f"{path}: read-only attribute cannot be restored without adding rights; "
                "preserve the evidence and arrange explicit owner-authorized metadata repair. "
                f"{error}"
            ) from error
        raise

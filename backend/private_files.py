"""Owner-only files and folders, on Linux, macOS and Windows.

Unix file modes mean nothing on Windows: os.open ignores its mode, chmod only
toggles read-only, and stat reports 0o666 for any writable file. Who can read a
file there is decided by its access list, so that's what is set and checked.
"""
import ctypes
import os
import stat
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

WINDOWS = os.name == "nt"

# SYSTEM and Administrators can reach any file on Windows anyway, as root can on
# Linux, so their entries don't make a file less private than chmod 600 does.
TRUSTED_SIDS = {"S-1-5-18", "S-1-5-32-544"}

_FILE_OBJECT = 1
_OWNER_INFORMATION = 1
_DACL_INFORMATION = 4
_PROTECTED_DACL = 0x80000000
_ALLOW_ENTRY = 0
_DENY_ENTRY = 1
_INHERIT_ONLY = 0x08


class _Acl(ctypes.Structure):
    _fields_ = [
        ("revision", ctypes.c_ubyte),
        ("padding", ctypes.c_ubyte),
        ("size", ctypes.c_ushort),
        ("entry_count", ctypes.c_ushort),
        ("padding2", ctypes.c_ushort),
    ]


class _EntryHeader(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("size", ctypes.c_ushort)]


@lru_cache(maxsize=1)
def _windows_api() -> Tuple["ctypes.WinDLL", "ctypes.WinDLL"]:
    pointer = ctypes.c_void_p
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.POINTER(pointer),
        ctypes.POINTER(pointer),
        ctypes.POINTER(pointer),
        ctypes.POINTER(pointer),
        ctypes.POINTER(pointer),
    ]
    advapi32.GetNamedSecurityInfoW.restype = ctypes.c_ulong
    advapi32.GetAce.argtypes = [pointer, ctypes.c_ulong, ctypes.POINTER(pointer)]
    advapi32.GetAce.restype = ctypes.c_int
    advapi32.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(pointer)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_int
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.POINTER(pointer),
        pointer,
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = ctypes.c_int
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        pointer,
        ctypes.POINTER(ctypes.c_long),
        ctypes.POINTER(pointer),
        ctypes.POINTER(ctypes.c_long),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = ctypes.c_int
    advapi32.SetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_ulong,
        pointer,
        pointer,
        pointer,
        pointer,
    ]
    advapi32.SetNamedSecurityInfoW.restype = ctypes.c_ulong
    kernel32.LocalFree.argtypes = [pointer]
    kernel32.LocalFree.restype = pointer
    return advapi32, kernel32


def _sid_text(sid: int) -> str:
    advapi32, kernel32 = _windows_api()
    text = ctypes.c_void_p()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.wstring_at(text.value)
    finally:
        kernel32.LocalFree(text)


def _windows_access(path: Path) -> Tuple[str, Optional[List[str]]]:
    """The owner, and everyone allowed in. None means the list can't vouch for the file."""
    advapi32, kernel32 = _windows_api()
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    failure = advapi32.GetNamedSecurityInfoW(
        str(path),
        _FILE_OBJECT,
        _OWNER_INFORMATION | _DACL_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if failure:
        raise ctypes.WinError(failure)
    try:
        owner_sid = _sid_text(owner.value)
        if not dacl.value:  # no access list at all lets everyone in
            return owner_sid, None
        allowed: List[str] = []
        for index in range(_Acl.from_address(dacl.value).entry_count):
            entry = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(entry)):
                raise ctypes.WinError(ctypes.get_last_error())
            header = _EntryHeader.from_address(entry.value)
            if header.flags & _INHERIT_ONLY or header.type == _DENY_ENTRY:
                continue  # these give nobody access to this object
            if header.type != _ALLOW_ENTRY:
                return owner_sid, None  # a kind of entry not understood here
            # An allow entry is its header, then a 4-byte access mask, then the SID.
            allowed.append(_sid_text(entry.value + ctypes.sizeof(_EntryHeader) + 4))
        return owner_sid, allowed
    finally:
        kernel32.LocalFree(descriptor)


def _allow_only(path: Path, sid: str) -> None:
    """Replaces the whole access list, so no entry has to be named to go.

    Windows folders can pass down entries for accounts that no longer exist, and
    icacls can't remove those by SID (error 1332).
    """
    advapi32, kernel32 = _windows_api()
    inherited_by = "OICI" if path.is_dir() else ""  # a folder's contents inherit it
    descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
    present, defaulted = ctypes.c_long(), ctypes.c_long()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"D:P(A;{inherited_by};FA;;;{sid})", 1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        failure = advapi32.SetNamedSecurityInfoW(
            str(path), _FILE_OBJECT, _DACL_INFORMATION | _PROTECTED_DACL, None, None, dacl, None
        )
        if failure:
            raise ctypes.WinError(failure)
    finally:
        kernel32.LocalFree(descriptor)


def is_private(path: Path) -> bool:
    """True if only the owner can reach this file or folder. Raises OSError if it can't tell."""
    if not WINDOWS:
        return not stat.S_IMODE(path.stat().st_mode) & 0o077
    owner, allowed = _windows_access(path)
    return allowed is not None and set(allowed) <= {owner, *TRUSTED_SIDS}


def make_private(path: Path) -> None:
    """Restricts a file or folder to its owner. Raises OSError if that fails."""
    if not WINDOWS:
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    if is_private(path):
        return
    owner, _ = _windows_access(path)
    _allow_only(path, owner)


def create_private_dir(path: Path) -> None:
    """Makes a folder if needed. A folder that already exists keeps its permissions.

    A key file may be saved somewhere like a home folder, and tightening that
    would lock everyone else out of it. mkdir treats existing folders the same way.
    """
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if WINDOWS and not existed:
        make_private(path)

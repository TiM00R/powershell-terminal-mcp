"""
proc_tree.py
Find and end the programs the PowerShell session started (its child processes),
so a program that ignores Ctrl+C can still be stopped without killing the
session itself. Windows only, standard library only (ctypes + Toolhelp32).

Windows records a process's parent by process ID, and IDs are reused. A
process whose real parent has exited can therefore look like the child of an
unrelated newer process that got the same ID. To avoid ending such a process,
a child only counts if it started after its parent.
"""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Console hosts belong to the terminal plumbing, not to the user's program.
_SKIP = {"conhost.exe", "openconsole.exe"}


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_k32 = None


def _kernel32():
    """kernel32 with argument and return types declared, so handles are passed
    as full pointer-sized values on 64-bit Windows."""
    global _k32
    if _k32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k.Process32FirstW.restype = wintypes.BOOL
        k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k.Process32NextW.restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.GetProcessTimes.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        _k32 = k
    return _k32


def _snapshot():
    """Return [(pid, parent_pid, exe_name)] for every running process."""
    k32 = _kernel32()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    out = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID,
                        entry.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _start_time(pid):
    """Process creation time (100 ns units), or None if it cannot be read."""
    k32 = _kernel32()
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exited),
                                   ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        k32.CloseHandle(h)


def child_processes(pid):
    """All processes started (directly or indirectly) by pid, each listed
    before its own children. Console hosts are left out of the result (their
    children are still searched). A process only counts as a child if it
    started after its parent, and if that cannot be checked it is left out:
    better to miss a program than to end an unrelated one."""
    if not pid:
        return []
    procs = _snapshot()
    found = []
    seen = {pid}
    todo = [(pid, _start_time(pid))]
    while todo:
        parent, parent_start = todo.pop()
        if parent_start is None:
            continue
        for cpid, ppid, name in procs:
            if ppid != parent or cpid in seen:
                continue
            child_start = _start_time(cpid)
            if child_start is None or child_start < parent_start:
                continue        # unreadable, or a stale parent ID: not ours
            seen.add(cpid)
            if name.lower() not in _SKIP:
                found.append((cpid, name))
            todo.append((cpid, child_start))
    return found


def end_child_processes(pid):
    """End every program started by pid (not pid itself). Returns the names of
    the processes that were ended."""
    k32 = _kernel32()
    ended = []
    for cpid, name in child_processes(pid):
        h = k32.OpenProcess(PROCESS_TERMINATE, False, cpid)
        if not h:
            continue
        try:
            if k32.TerminateProcess(h, 1):
                ended.append(name)
        except Exception:
            logger.debug("could not end %s (%s)", name, cpid, exc_info=True)
        finally:
            k32.CloseHandle(h)
    return ended

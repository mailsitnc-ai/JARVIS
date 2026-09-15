"""Small Windows system queries used by doctor."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass
class MemoryStatus:
    total_gb: float
    available_gb: float
    load_percent: int


def memory_status() -> MemoryStatus | None:
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    gb = 1024 ** 3
    return MemoryStatus(status.ullTotalPhys / gb, status.ullAvailPhys / gb, int(status.dwMemoryLoad))

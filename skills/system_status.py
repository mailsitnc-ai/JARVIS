"""Battery, CPU, RAM and disk status for this Windows PC."""
import ctypes
import re
import shutil
import time
from ctypes import wintypes

SKILL = {
    "name": "system_status",
    "description": "Report battery level, CPU load, RAM use and free disk space on this PC.",
    "triggers": [
        r"\bbattery\b",
        r"\b(?:cpu|processor|ram|memory|disk|storage)\s+(?:usage|use|load|space|status|left|free|info)\b",
        r"\bsystem\s+(?:status|info|health|stats)\b",
        r"\bhow\s+much\s+(?:ram|memory|disk|storage|space)\b",
    ],
    "version": 1,
    "origin": "builtin",
}


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + \
               [(name, ctypes.c_ulonglong) for name in ("TotalPhys", "AvailPhys", "TotalPageFile", "AvailPageFile",
                                                        "TotalVirtual", "AvailVirtual", "AvailExtendedVirtual")]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


def _battery():
    status = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return "Battery: unknown."
    if status.BatteryFlag == 128 or status.BatteryLifePercent == 255:
        return "Battery: no battery detected."
    plugged = "plugged in" if status.ACLineStatus == 1 else "on battery"
    remaining = ""
    if status.ACLineStatus != 1 and status.BatteryLifeTime != 0xFFFFFFFF:
        minutes = status.BatteryLifeTime // 60
        remaining = f", about {minutes // 60}h {minutes % 60:02d}m left"
    return f"Battery: {status.BatteryLifePercent}% ({plugged}{remaining})."


def _memory():
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    gb = 1024 ** 3
    return f"RAM: {status.AvailPhys / gb:.1f} GB free of {status.TotalPhys / gb:.1f} GB ({status.dwMemoryLoad}% in use)."


def _cpu():
    def sample():
        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        return [(t.high << 32) | t.low for t in (idle, kernel, user)]

    idle1, kernel1, user1 = sample()
    time.sleep(0.3)
    idle2, kernel2, user2 = sample()
    total = (kernel2 - kernel1) + (user2 - user1)
    busy = total - (idle2 - idle1)
    return f"CPU: {100 * busy / total:.0f}% busy." if total else "CPU: unknown."


def _disk():
    usage = shutil.disk_usage("C:\\")
    gb = 1024 ** 3
    return f"Disk C: {usage.free / gb:.1f} GB free of {usage.total / gb:.1f} GB."


def run(request, context):
    text = request.lower()
    parts = {
        "battery": (r"\bbattery\b", _battery),
        "cpu": (r"\b(?:cpu|processor)\b", _cpu),
        "ram": (r"\b(?:ram|memory)\b", _memory),
        "disk": (r"\b(?:disk|storage|space)\b", _disk),
    }
    wanted = [fn for pattern, fn in parts.values() if re.search(pattern, text)]
    return " ".join(fn() for fn in (wanted or [fn for _, fn in parts.values()]))

"""Battery, CPU, RAM and disk status. Cross-platform (Windows / macOS / Linux)."""
import os
import re
import shutil
import subprocess
import time

from core.oslayer import IS_MAC, IS_WINDOWS, memory_gb

SKILL = {
    "name": "system_status",
    "description": "Report battery level, CPU load, RAM use and free disk space on this computer.",
    "triggers": [
        r"\bbattery\b",
        r"\b(?:cpu|processor|ram|memory|disk|storage)\s+(?:usage|use|load|space|status|left|free|info)\b",
        r"\bsystem\s+(?:status|info|health|stats)\b",
        r"\bhow\s+much\s+(?:ram|memory|disk|storage|space)\b",
    ],
    "version": 2,
    "origin": "builtin",
}
_GB = 1024 ** 3


def _battery():
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        class _PS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
        st = _PS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return "Battery: unknown."
        if st.BatteryFlag == 128 or st.BatteryLifePercent == 255:
            return "Battery: no battery detected."
        plugged = "plugged in" if st.ACLineStatus == 1 else "on battery"
        remaining = ""
        if st.ACLineStatus != 1 and st.BatteryLifeTime != 0xFFFFFFFF:
            minutes = st.BatteryLifeTime // 60
            remaining = f", about {minutes // 60}h {minutes % 60:02d}m left"
        return f"Battery: {st.BatteryLifePercent}% ({plugged}{remaining})."
    if IS_MAC:
        try:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return "Battery: unknown."
        pct = re.search(r"(\d+)%", out)
        if not pct:
            return "Battery: no battery detected."
        charging = "charging" in out.lower() or "AC Power" in out
        left = re.search(r"(\d+:\d+)\s+remaining", out)
        extra = f", {left.group(1)} left" if left and not charging else ""
        return f"Battery: {pct.group(1)}% ({'charging/plugged in' if charging else 'on battery'}{extra})."
    # Linux
    for base in ("/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT1"):
        try:
            cap = int(open(f"{base}/capacity").read().strip())
            status = open(f"{base}/status").read().strip().lower()
            return f"Battery: {cap}% ({status})."
        except OSError:
            continue
    return "Battery: no battery detected."


def _memory():
    values = memory_gb()
    if not values:
        return "RAM: unknown."
    total, avail, load = values
    return f"RAM: {avail:.1f} GB free of {total:.1f} GB ({load}% in use)."


def _cpu():
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        class _FT(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        def sample():
            idle, kernel, user = _FT(), _FT(), _FT()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
            return [(t.high << 32) | t.low for t in (idle, kernel, user)]

        idle1, kernel1, user1 = sample()
        time.sleep(0.3)
        idle2, kernel2, user2 = sample()
        total = (kernel2 - kernel1) + (user2 - user1)
        busy = total - (idle2 - idle1)
        return f"CPU: {100 * busy / total:.0f}% busy." if total else "CPU: unknown."
    try:
        load1 = os.getloadavg()[0]
        cores = os.cpu_count() or 1
        return f"CPU: load {load1:.2f} over {cores} core(s) ({min(100, 100 * load1 / cores):.0f}%)."
    except (OSError, AttributeError):
        return "CPU: unknown."


def _disk():
    root = "C:\\" if IS_WINDOWS else "/"
    usage = shutil.disk_usage(root)
    return f"Disk {root}: {usage.free / _GB:.1f} GB free of {usage.total / _GB:.1f} GB."


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

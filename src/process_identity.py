"""Portable process-birth identity for PID-owned ephemeral resources."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _darwin_start_time(pid: int) -> Optional[tuple[int, int]]:
    """Kernel process start timestamp with microsecond precision on macOS."""
    try:
        import ctypes

        class ProcBsdInfo(ctypes.Structure):
            _fields_ = [
                ("header", ctypes.c_uint32 * 12),
                ("pbi_comm", ctypes.c_char * 16),
                ("pbi_name", ctypes.c_char * 32),
                ("tail", ctypes.c_uint32 * 5),
                ("pbi_nice", ctypes.c_int32),
                ("pbi_start_tvsec", ctypes.c_uint64),
                ("pbi_start_tvusec", ctypes.c_uint64),
            ]

        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libproc.proc_pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_int,
        ]
        libproc.proc_pidinfo.restype = ctypes.c_int
        info = ProcBsdInfo()
        size = ctypes.sizeof(info)
        read = libproc.proc_pidinfo(int(pid), 3, 0, ctypes.byref(info), size)
        if read != size or not info.pbi_start_tvsec:
            return None
        return int(info.pbi_start_tvsec), int(info.pbi_start_tvusec)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def process_start_token(pid: int) -> Optional[str]:
    """Stable token for one PID incarnation, or None when unavailable."""
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
            fields = stat.rsplit(")", 1)[1].strip().split()
            start_ticks = fields[19]  # proc(5) field 22; tail begins at field 3
            try:
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                    encoding="utf-8"
                ).strip()
            except OSError:
                return None
            if not boot_id:
                return None
            return _digest(f"linux:{boot_id}:{start_ticks}")
        except (OSError, ValueError, IndexError):
            # Never cross token namespaces for a live Linux process. A
            # transient /proc failure is ambiguity, not proof of PID reuse.
            return None

    if sys.platform == "darwin":
        started = _darwin_start_time(pid)
        if started is None:
            return None
        return _digest(f"darwin:{started[0]}:{started[1]}")

    try:
        stable_env = os.environ.copy()
        stable_env.update({"TZ": "UTC", "LC_ALL": "C", "LANG": "C"})
        proc = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(int(pid))],
            capture_output=True, text=True, timeout=2, env=stable_env,
        )
        started = (proc.stdout or "").strip()
        if proc.returncode == 0 and started:
            return _digest(f"ps:{started}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OverflowError):
        return False
    except PermissionError:
        return True


def owner_is_current(
    pid: int,
    recorded_token: Optional[str],
    *,
    alive: Callable[[int], bool] = pid_alive,
    token_reader: Callable[[int], Optional[str]] = process_start_token,
) -> bool:
    """Return liveness conservatively while detecting proven PID reuse."""
    if not alive(pid):
        return False
    if not recorded_token:
        return True  # legacy ownership record: retain on ambiguity
    current = token_reader(pid)
    if not current:
        return True  # platform cannot prove reuse: retain
    return str(recorded_token) == current

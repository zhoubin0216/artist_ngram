from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def run_version(command: list[str], timeout: int = 10) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        raw = (completed.stdout or b"") + (completed.stderr or b"")
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw[:20]:
            output = raw.decode("utf-16", errors="replace").strip()
        else:
            output = raw.decode("utf-8", errors="replace").strip()
        return {"returncode": completed.returncode, "output": output}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "output": str(exc)}


def cpu_name() -> str:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        )
        return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        return platform.processor()


def memory() -> dict[str, float]:
    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return {
        "total_gb": round(status.ullTotalPhys / 2**30, 2),
        "available_gb": round(status.ullAvailPhys / 2**30, 2),
    }


def main() -> None:
    java = Path(r"C:\Program Files\Java\jre1.8.0_501\bin\java.exe")
    disk = shutil.disk_usage(ROOT)
    wsl = run_version(["wsl.exe", "--status"])
    report = {
        "os": {
            "description": platform.platform(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.machine(),
        },
        "cpu": {"name": cpu_name(), "logical_processors": os.cpu_count()},
        "memory": memory(),
        "project_disk": {
            "root": ROOT.drive,
            "total_gb": round(disk.total / 2**30, 2),
            "free_gb": round(disk.free / 2**30, 2),
        },
        "python": {"version": sys.version, "executable": sys.executable},
        "java": run_version([str(java), "-version"]) if java.exists() else None,
        "git": run_version(["git", "--version"]),
        "pyspark_available": importlib.util.find_spec("pyspark") is not None,
        "wsl": wsl,
        "hdfs_available": shutil.which("hdfs") is not None,
        "local_spark_suitable": memory()["total_gb"] >= 8 and (os.cpu_count() or 0) >= 4,
    }
    output = ROOT / "outputs" / "environment_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

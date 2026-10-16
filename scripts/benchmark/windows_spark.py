from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def prepare_windows_spark_runtime() -> Path | None:
    """Return an ASCII-only SPARK_HOME when the venv lives under a Unicode path."""
    if os.name != "nt":
        return None
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    base_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    worker_env = Path(tempfile.gettempdir()) / (
        f"artist_ngram_python_worker_{sys.version_info.major}_{sys.version_info.minor}"
    )
    worker_python = worker_env / "Scripts" / "python.exe"
    if not worker_python.exists():
        subprocess.run(
            [str(base_python), "-m", "venv", str(worker_env)],
            check=True,
            capture_output=True,
        )
    os.environ.setdefault("PYSPARK_PYTHON", str(worker_python))
    import pyspark

    source = Path(pyspark.__file__).resolve().parent
    if str(source).isascii():
        os.environ.setdefault("SPARK_HOME", str(source))
        return source

    version = getattr(pyspark, "__version__", "unknown").replace(".", "_")
    target = Path(tempfile.gettempdir()) / f"artist_ngram_spark_runtime_{version}"
    marker = target / ".source-version"
    if not marker.exists() or marker.read_text(encoding="ascii", errors="ignore") != version:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        marker.write_text(version, encoding="ascii")
    os.environ["SPARK_HOME"] = str(target)
    return target

# Local environment

The final experiment environment is this Windows computer only. Results from other computers must not be merged into the benchmark table.

Observed on 2026-10-13:

- Windows 10.0.26200, x64.
- CPU: Intel Core Ultra 9 275HX, 24 logical processors.
- Project disk: D: with approximately 278 GB free before the pilot.
- System Python: Miniconda Python 3.13.12; an installed Python 3.9.13 is used to create the project virtual environment for PySpark compatibility.
- Java: Oracle Java 8 Update 501 at `C:\Program Files\Java\jre1.8.0_501`. The Oracle PATH shim can hang, so the benchmark sets `JAVA_HOME` to the direct JRE path.
- Git: 2.53.0.windows.2.
- WSL2 Ubuntu: not installed. `wsl --status` reports that WSL must be installed.
- HDFS: not configured.
- PySpark: installed in `.venv` from `requirements.txt`.
- Git working tree: clean immediately after cloning; all current uncommitted changes are the Getty/benchmark implementation from this task. No commit, push, or pull request was created.

The project path contains non-ASCII characters. Java 8 cannot load Spark's jar wildcard from that path, so `scripts/benchmark/windows_spark.py` reproducibly copies the installed PySpark runtime to an ASCII-only `%TEMP%` cache and sets `SPARK_HOME`. It also creates an ASCII-only Python 3.9 worker environment. Getty data, benchmark inputs, and results remain in the project directory.

PySpark's Hadoop local-file writer and directory listing on Windows require native Hadoop helpers that are not bundled with PySpark. The benchmark therefore generates equivalent CSV, JSON, and Parquet fixtures plus exact-file manifests once with pandas/PyArrow, outside the timed section. Spark performs all timed reads, joins, filters, and aggregations. For partitioned Q2, the manifest selects only files in requested year directories. No unverified `winutils` binary is downloaded, and this does not emulate HDFS.

This hardware is suitable for reliable local single-node Spark experiments. The checked-in benchmark configuration deliberately uses four local worker threads and 4 GB driver memory so runs are repeatable and leave headroom for Windows.

Regenerate the machine-readable report with:

```powershell
.\.venv\Scripts\python.exe scripts\collect_environment.py
```

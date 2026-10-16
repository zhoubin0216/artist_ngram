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
- Git synchronization: the team's OpenAlex and Ngram work was fast-forwarded from `origin/main`, then the Getty/benchmark implementation was merged and pushed as commit `d60aadb` on 2026-10-16. No pull request was created because the user explicitly requested a direct push.

The project path contains non-ASCII characters. Java 8 cannot load Spark's jar wildcard from that path, so `scripts/benchmark/windows_spark.py` reproducibly copies the installed PySpark runtime to an ASCII-only `%TEMP%` cache and sets `SPARK_HOME`. It also creates an ASCII-only Python 3.9 worker environment. Getty data, benchmark inputs, and results remain in the project directory.

PySpark's Hadoop local-file writer and directory listing on Windows require native Hadoop helpers that are not bundled with PySpark. The benchmark therefore generates equivalent CSV, JSON, and Parquet fixtures plus exact-file manifests once with pandas/PyArrow, outside the timed section. Spark performs all timed reads, joins, filters, and aggregations. For partitioned Q2, the manifest selects only files in requested year directories. No unverified `winutils` binary is downloaded, and this does not emulate HDFS.

This hardware is suitable for reliable local single-node Spark experiments. Both checked-in benchmark configurations deliberately use four local worker threads and 4 GB driver memory so runs are repeatable and leave headroom for Windows. Getty synthetic and OpenAlex real-data results are stored separately and were both measured only on this machine.

Regenerate the machine-readable report with:

```powershell
.\.venv\Scripts\python.exe scripts\collect_environment.py
```

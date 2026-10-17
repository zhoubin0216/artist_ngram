from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from queries import checksum_frame, run_query
from windows_spark import prepare_windows_spark_runtime


ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve(path: str) -> Path:
    return (ROOT / path).resolve()


def directory_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def spark_session(config: dict[str, Any]) -> SparkSession:
    spark_cfg = config["spark"]
    prepare_windows_spark_runtime()
    java_home = Path(r"C:\Program Files\Java\jre1.8.0_501")
    if "JAVA_HOME" not in os.environ and java_home.exists():
        os.environ["JAVA_HOME"] = str(java_home)
        os.environ["PATH"] = f"{java_home / 'bin'};{os.environ.get('PATH', '')}"
    builder = (
        SparkSession.builder.appName(spark_cfg["app_name"])
        .master(spark_cfg["master"])
        .config("spark.driver.memory", spark_cfg["driver_memory"])
        .config("spark.executor.memory", spark_cfg["executor_memory"])
        .config("spark.sql.shuffle.partitions", spark_cfg["shuffle_partitions"])
        .config("spark.default.parallelism", spark_cfg["default_parallelism"])
        .config("spark.sql.adaptive.enabled", str(spark_cfg["adaptive_enabled"]).lower())
        .config(
            "spark.sql.autoBroadcastJoinThreshold",
            spark_cfg["auto_broadcast_join_threshold"],
        )
        .config("spark.sql.session.timeZone", spark_cfg["session_timezone"])
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.ui.enabled", "false")
    )
    session = builder.getOrCreate()
    session.sparkContext.setLogLevel("WARN")
    return session


def write_formats(
    source_path: Path, multiplier: int, base: Path
) -> tuple[dict[str, Path], int]:
    """Create identical storage fixtures outside the timed Spark workload.

    Spark's Hadoop local-file writer on Windows requires native Hadoop helpers that
    are not distributed with PySpark.  PyArrow writes standards-compliant Parquet
    (including Hive year partitions), while pandas writes the CSV/JSON equivalents.
    Spark performs every timed read, transformation, join, and aggregation.
    """
    if multiplier < 1:
        raise ValueError("synthetic multiplier must be at least 1")
    source = pd.read_parquet(source_path)
    if source.empty:
        raise RuntimeError("Getty events are empty; run the Getty pilot first")
    copies: list[pd.DataFrame] = []
    for copy_id in range(multiplier):
        frame = source.copy()
        suffix = f"#benchmark-copy-{copy_id}"
        frame["object_id"] = frame["object_id"].fillna("").astype(str) + suffix
        frame["event_id"] = frame["event_id"].fillna("").astype(str) + suffix
        frame["synthetic_benchmark"] = multiplier > 1
        frame["benchmark_copy_id"] = copy_id
        copies.append(frame)
    events = pd.concat(copies, ignore_index=True)
    # The cleaned CSV represents missing strings as empty fields while Parquet
    # retains an empty string. Normalize both to a single logical null before
    # writing the equivalent benchmark fixtures.
    string_columns = events.select_dtypes(include=["object", "string"]).columns
    events[string_columns] = events[string_columns].replace(r"^\s*$", None, regex=True)

    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    paths = {
        "csv": base / "csv",
        "json": base / "json",
        "parquet_unpartitioned": base / "parquet_unpartitioned",
        "parquet_partitioned": base / "parquet_partitioned",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    events.to_csv(paths["csv"] / "events.csv", index=False)
    events.to_json(
        paths["json"] / "events.jsonl",
        orient="records",
        lines=True,
        force_ascii=False,
    )
    events.to_parquet(
        paths["parquet_unpartitioned"] / "events.parquet", index=False
    )
    arrow_schema = pa.Table.from_pandas(events, preserve_index=False).schema
    for partition_value, frame in events.groupby("event_year", dropna=False):
        label = (
            "__HIVE_DEFAULT_PARTITION__"
            if pd.isna(partition_value)
            else str(int(partition_value))
        )
        partition_dir = paths["parquet_partitioned"] / f"event_year={label}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        # Keep event_year in each file because the Windows compatibility manifest
        # passes exact files to Spark rather than asking Hadoop to list directories.
        pq.write_table(
            pa.Table.from_pandas(
                frame, schema=arrow_schema, preserve_index=False, safe=True
            ),
            partition_dir / "events.parquet",
        )
    return paths, len(events)


def build_manifests(paths: dict[str, Path]) -> dict[str, list[Path]]:
    extensions = {"csv": "*.csv", "json": "*.jsonl"}
    manifests: dict[str, list[Path]] = {}
    for name, path in paths.items():
        pattern = extensions.get(name, "*.parquet")
        manifests[name] = sorted(path.rglob(pattern))
        if not manifests[name]:
            raise RuntimeError(f"No files found for benchmark format {name}: {path}")
    return manifests


def read_format(
    spark: SparkSession, name: str, files: list[Path], schema
) -> DataFrame:
    file_names = [str(path) for path in files]
    if name == "csv":
        return spark.read.option("header", True).schema(schema).csv(file_names)
    if name == "json":
        return spark.read.schema(schema).json(file_names)
    return spark.read.parquet(*file_names)


def select_files(
    format_name: str,
    files: list[Path],
    query_name: str,
    year_start: int,
    year_end: int,
) -> list[Path]:
    if format_name != "parquet_partitioned" or query_name != "q2":
        return files
    selected: list[Path] = []
    for path in files:
        label = path.parent.name.removeprefix("event_year=")
        if label == "__HIVE_DEFAULT_PARTITION__":
            continue
        if year_start <= int(label) <= year_end:
            selected.append(path)
    return selected


def execute_once(
    spark: SparkSession,
    format_name: str,
    format_files: list[Path],
    schema,
    artists: DataFrame,
    query_name: str,
    artist_id: str,
    year_start: int,
    year_end: int,
    broadcast_join: bool,
) -> tuple[float, int, str, str]:
    started = time.perf_counter()
    selected_files = select_files(
        format_name, format_files, query_name, year_start, year_end
    )
    events = read_format(spark, format_name, selected_files, schema)
    result = run_query(
        query_name,
        events,
        artists,
        artist_id=artist_id,
        year_start=year_start,
        year_end=year_end,
        broadcast_join=broadcast_join,
    )
    plan = result._jdf.queryExecution().executedPlan().toString()
    output_rows, checksum = checksum_frame(result)
    return time.perf_counter() - started, output_rows, checksum, plan


def write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def validate_results(rows: list[dict[str, Any]]) -> None:
    format_rows = [row for row in rows if row["comparison"] == "storage_format"]
    for query_name in sorted({row["query"] for row in format_rows}):
        checks = {
            (row["output_rows"], row["checksum"])
            for row in format_rows
            if row["query"] == query_name
        }
        if len(checks) != 1:
            raise RuntimeError(f"Result mismatch across formats for {query_name}: {checks}")
    join_rows = [row for row in rows if row["comparison"] == "join_strategy"]
    checks = {(row["output_rows"], row["checksum"]) for row in join_rows}
    if len(checks) != 1:
        raise RuntimeError(f"Result mismatch between join strategies: {checks}")


def write_chart(path: Path, rows: list[dict[str, Any]]) -> None:
    storage = [row for row in rows if row["comparison"] == "storage_format"]
    labels = [f"{row['format']}\n{row['query'].upper()}" for row in storage]
    values = [float(row["median_seconds"]) for row in storage]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(range(len(values)), values, color="#35618F")
    ax.set_ylabel("Median wall time (seconds)")
    ax.set_title("Spark storage-format benchmark")
    ax.set_xticks(range(len(labels)), labels, rotation=60, ha="right")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def write_summary(
    path: Path,
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    spark: SparkSession,
) -> None:
    storage = [row for row in rows if row["comparison"] == "storage_format"]
    joins = [row for row in rows if row["comparison"] == "join_strategy"]
    storage_by_key = {
        (row["format"], row["query"]): row for row in storage
    }
    csv_bytes = int(storage_by_key[("csv", "q1")]["storage_bytes"])
    parquet_bytes = int(
        storage_by_key[("parquet_unpartitioned", "q1")]["storage_bytes"]
    )
    partitioned_bytes = int(
        storage_by_key[("parquet_partitioned", "q1")]["storage_bytes"]
    )
    q2_unpartitioned = float(
        storage_by_key[("parquet_unpartitioned", "q2")]["median_seconds"]
    )
    q2_partitioned = float(
        storage_by_key[("parquet_partitioned", "q2")]["median_seconds"]
    )
    standard_join = next(
        float(row["median_seconds"])
        for row in joins
        if row["join_strategy"] == "standard"
    )
    broadcast_join = next(
        float(row["median_seconds"])
        for row in joins
        if row["join_strategy"] == "broadcast"
    )
    join_change = 100 * (broadcast_join / standard_join - 1)
    join_finding = (
        f"Broadcast join was {join_change:.1f}% slower than the standard join"
        if join_change >= 0
        else f"Broadcast join was {-join_change:.1f}% faster than the standard join"
    )
    lines = [
        "# Benchmark summary",
        "",
        "All timings were measured on this Windows computer. API request time is excluded.",
        "The benchmark input is explicitly synthetic: cleaned Getty rows are duplicated with unique benchmark IDs.",
        "Storage fixtures and exact-file manifests were generated once with pandas/PyArrow outside all timings; Spark performs every measured read and query.",
        "On Windows, the year-partitioned Q2 manifest selects only requested year directories, equivalent to partition pruning without Hadoop directory-list JNI.",
        "",
        "## Fixed configuration",
        "",
        f"- Spark version: {spark.version}",
        f"- Master: `{config['spark']['master']}`",
        f"- Driver memory: `{config['spark']['driver_memory']}`",
        f"- Shuffle partitions: `{config['spark']['shuffle_partitions']}`",
        f"- Adaptive execution: `{config['spark']['adaptive_enabled']}`",
        f"- Synthetic multiplier: `{config['benchmark']['synthetic_multiplier']}`",
        f"- Warm-up runs: `{config['benchmark']['warmup_runs']}`",
        f"- Measured runs: `{config['benchmark']['measured_runs']}`",
        "",
        "## Findings",
        "",
        f"- Unpartitioned Parquet used {100 * (1 - parquet_bytes / csv_bytes):.1f}% less disk space than CSV; year-partitioned Parquet used {100 * (1 - partitioned_bytes / csv_bytes):.1f}% less.",
        f"- For Q2 (1800-1950), year-partitioned Parquet was {100 * (1 - q2_partitioned / q2_unpartitioned):.1f}% faster than unpartitioned Parquet.",
        f"- {join_finding} in this local benchmark; broadcasting is not automatically beneficial at every scale.",
        "- These are engineering measurements on synthetic duplicates, not additional historical observations.",
        "",
        "## Storage formats",
        "",
        "| Format | Query | Input rows | Storage bytes | Median seconds | Output rows | Validated |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in storage:
        lines.append(
            f"| {row['format']} | {row['query'].upper()} | {row['input_rows']} | "
            f"{row['storage_bytes']} | {row['median_seconds']} | {row['output_rows']} | yes |"
        )
    lines.extend(
        [
            "",
            "## Join strategies",
            "",
            "| Strategy | Query | Median seconds | Output rows | Validated |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for row in joins:
        lines.append(
            f"| {row['join_strategy']} | {row['query'].upper()} | "
            f"{row['median_seconds']} | {row['output_rows']} | yes |"
        )
    lines.extend(
        [
            "",
            "Shuffle read/write bytes are left blank in the CSV because PySpark 3.5 does not expose stable per-query task metrics through a public Python API. Physical plans are saved separately for audit.",
            "HDFS was not tested because this machine does not have an HDFS environment configured.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Spark storage and join benchmarks")
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "benchmark.yaml"
    )
    parser.add_argument(
        "--synthetic-multiplier",
        type=int,
        default=None,
        help="Override the configured duplication factor",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    if args.synthetic_multiplier is not None:
        config["benchmark"]["synthetic_multiplier"] = args.synthetic_multiplier

    multiplier = int(config["benchmark"]["synthetic_multiplier"])
    format_paths, input_rows = write_formats(
        resolve(config["paths"]["events"]),
        multiplier,
        resolve(config["paths"]["generated_data"]),
    )
    format_manifests = build_manifests(format_paths)
    spark = spark_session(config)
    try:
        artists = spark.read.option("header", True).option("inferSchema", True).csv(
            str(resolve(config["paths"]["artists"]))
        )
        benchmark_events = read_format(
            spark,
            "parquet_unpartitioned",
            format_manifests["parquet_unpartitioned"],
            None,
        )
        schema = benchmark_events.schema
        spark_input_rows = benchmark_events.count()
        if spark_input_rows != input_rows:
            raise RuntimeError(
                f"Storage fixture row mismatch: pandas={input_rows}, Spark={spark_input_rows}"
            )
        artist_id = (
            benchmark_events.groupBy("artist_id")
            .count()
            .orderBy(F.desc("count"), "artist_id")
            .first()["artist_id"]
        )
        plans_dir = ROOT / "outputs" / "benchmark_plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        warmups = int(config["benchmark"]["warmup_runs"])
        measured = int(config["benchmark"]["measured_runs"])

        scenarios: list[tuple[str, str, str, bool]] = []
        for format_name in config["benchmark"]["formats"]:
            for query_name in config["benchmark"]["queries"]:
                scenarios.append(("storage_format", format_name, query_name, False))
        scenarios.extend(
            [
                ("join_strategy", "parquet_unpartitioned", "q3", False),
                ("join_strategy", "parquet_unpartitioned", "q3", True),
            ]
        )

        for comparison, format_name, query_name, broadcast_join in scenarios:
            format_path = format_paths[format_name]
            format_files = format_manifests[format_name]
            for _ in range(warmups):
                execute_once(
                    spark,
                    format_name,
                    format_files,
                    schema,
                    artists,
                    query_name,
                    artist_id,
                    int(config["benchmark"]["year_start"]),
                    int(config["benchmark"]["year_end"]),
                    broadcast_join,
                )
            timings: list[float] = []
            observed: list[tuple[int, str]] = []
            plan = ""
            for _ in range(measured):
                elapsed, output_rows, checksum, plan = execute_once(
                    spark,
                    format_name,
                    format_files,
                    schema,
                    artists,
                    query_name,
                    artist_id,
                    int(config["benchmark"]["year_start"]),
                    int(config["benchmark"]["year_end"]),
                    broadcast_join,
                )
                timings.append(elapsed)
                observed.append((output_rows, checksum))
            if len(set(observed)) != 1:
                raise RuntimeError(f"Unstable output for {format_name}/{query_name}")
            strategy = "broadcast" if broadcast_join else "standard"
            (plans_dir / f"{comparison}_{format_name}_{query_name}_{strategy}.txt").write_text(
                plan, encoding="utf-8"
            )
            rows.append(
                {
                    "comparison": comparison,
                    "format": format_name,
                    "query": query_name,
                    "join_strategy": strategy,
                    "synthetic_benchmark": True,
                    "synthetic_multiplier": config["benchmark"]["synthetic_multiplier"],
                    "input_rows": input_rows,
                    "output_rows": observed[0][0],
                    "checksum": observed[0][1],
                    "storage_bytes": directory_size(format_path),
                    "warmup_runs": warmups,
                    "measured_runs": measured,
                    "run_1_seconds": round(timings[0], 6),
                    "run_2_seconds": round(timings[1], 6),
                    "run_3_seconds": round(timings[2], 6),
                    "median_seconds": round(statistics.median(timings), 6),
                    "shuffle_read_bytes": "",
                    "shuffle_write_bytes": "",
                    "spark_version": spark.version,
                    "spark_master": config["spark"]["master"],
                    "spark_driver_memory": config["spark"]["driver_memory"],
                    "spark_shuffle_partitions": config["spark"]["shuffle_partitions"],
                }
            )

        validate_results(rows)
        write_results(resolve(config["paths"]["results"]), rows)
        write_summary(resolve(config["paths"]["summary"]), rows, config, spark)
        write_chart(resolve(config["paths"]["chart"]), rows)
        print(json.dumps({"rows": len(rows), "input_rows": input_rows}, indent=2))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

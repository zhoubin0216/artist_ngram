from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from pyspark.sql import DataFrame, SparkSession

from build_integrated_metrics import ROOT, build_metrics, resolve, write_fixtures
from integrated_queries import movement_join_aggregate, query_outputs, select_query
from queries import checksum_frame
from run_benchmark import directory_size, load_config, spark_session


def manifests(paths: dict[str, Path]) -> dict[str, list[Path]]:
    patterns = {"csv": "*.csv", "json": "*.jsonl"}
    result: dict[str, list[Path]] = {}
    for name, path in paths.items():
        result[name] = sorted(path.rglob(patterns.get(name, "*.parquet")))
        if not result[name]:
            raise RuntimeError(f"No integrated fixture files for {name}")
    return result


def years_for_query(query_name: str, config: dict[str, Any]) -> tuple[int, int]:
    benchmark = config["benchmark"]
    if query_name == "artist":
        return int(benchmark["artist_year_start"]), int(benchmark["artist_year_end"])
    if query_name == "movement":
        return int(benchmark["movement_year_start"]), int(benchmark["movement_year_end"])
    return int(benchmark["period_year_start"]), int(benchmark["period_year_end"])


def select_files(
    format_name: str,
    files: list[Path],
    query_name: str,
    config: dict[str, Any],
) -> list[Path]:
    if format_name != "parquet_partitioned":
        return files
    start, end = years_for_query(query_name, config)
    selected = [
        path
        for path in files
        if start <= int(path.parent.name.removeprefix("year=")) <= end
    ]
    if not selected:
        raise RuntimeError(f"No partitions selected for {query_name}: {start}-{end}")
    return selected


def read_format(
    spark: SparkSession, format_name: str, files: list[Path], schema
) -> DataFrame:
    names = [str(path) for path in files]
    if format_name == "csv":
        return spark.read.option("header", True).schema(schema).csv(names)
    if format_name == "json":
        return spark.read.schema(schema).json(names)
    return spark.read.parquet(*names)


def checksum_outputs(outputs: dict[str, DataFrame]) -> tuple[int, str, str]:
    total_rows = 0
    parts: list[str] = []
    plans: list[str] = []
    for name in ("ngram", "openalex", "getty", "summary"):
        row_count, checksum = checksum_frame(outputs[name])
        total_rows += row_count
        parts.append(f"{name}:{row_count}:{checksum}")
        plans.append(f"--- {name} ---\n{outputs[name]._jdf.queryExecution().executedPlan().toString()}")
    return total_rows, "|".join(parts), "\n".join(plans)


def execute_once(
    spark: SparkSession,
    format_name: str,
    files: list[Path],
    schema,
    query_name: str,
    config: dict[str, Any],
) -> tuple[float, int, str, str, int, int]:
    selected_files = select_files(format_name, files, query_name, config)
    started = time.perf_counter()
    metrics = read_format(spark, format_name, selected_files, schema)
    benchmark = config["benchmark"]
    selected = select_query(
        metrics,
        query_name,
        artist_id=benchmark["artist_id"],
        movement_id=benchmark["movement_id"],
        artist_year_start=int(benchmark["artist_year_start"]),
        artist_year_end=int(benchmark["artist_year_end"]),
        movement_year_start=int(benchmark["movement_year_start"]),
        movement_year_end=int(benchmark["movement_year_end"]),
        period_year_start=int(benchmark["period_year_start"]),
        period_year_end=int(benchmark["period_year_end"]),
    )
    output_rows, checksum, plan = checksum_outputs(query_outputs(selected))
    return (
        time.perf_counter() - started,
        output_rows,
        checksum,
        plan,
        len(selected_files),
        sum(path.stat().st_size for path in selected_files),
    )


def execute_join_once(
    metrics: DataFrame, bridge: DataFrame, broadcast_join: bool
) -> tuple[float, int, str, str]:
    started = time.perf_counter()
    result = movement_join_aggregate(metrics, bridge, broadcast_join=broadcast_join)
    plan = result._jdf.queryExecution().executedPlan().toString()
    rows, checksum = checksum_frame(result)
    return time.perf_counter() - started, rows, checksum, plan


def validate_results(rows: list[dict[str, Any]]) -> None:
    storage = [row for row in rows if row["comparison"] == "storage_format"]
    for query_name in {row["query"] for row in storage}:
        values = {
            (row["output_rows"], row["checksum"])
            for row in storage
            if row["query"] == query_name
        }
        if len(values) != 1:
            raise RuntimeError(f"Integrated result mismatch for {query_name}: {values}")
    joins = [row for row in rows if row["comparison"] == "join_strategy"]
    if len({(row["output_rows"], row["checksum"]) for row in joins}) != 1:
        raise RuntimeError("Integrated standard and broadcast joins disagree")


def write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_chart(path: Path, rows: list[dict[str, Any]]) -> None:
    storage = [row for row in rows if row["comparison"] == "storage_format"]
    labels = [f"{row['format']}\n{row['query']}" for row in storage]
    values = [float(row["median_seconds"]) for row in storage]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(range(len(values)), values, color="#6B5B95")
    ax.set_ylabel("Median wall time (seconds)")
    ax.set_title("Three-source query benchmark")
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
    validation: dict[str, Any],
    spark: SparkSession,
) -> None:
    storage = [row for row in rows if row["comparison"] == "storage_format"]
    joins = [row for row in rows if row["comparison"] == "join_strategy"]
    lines = [
        "# Three-source benchmark summary",
        "",
        "The benchmark covers Ngram, OpenAlex and Getty independently and produces a non-additive cross-source summary for every query.",
        "All measurements were made on the documented Windows computer. Fixture generation and network access are excluded.",
        "",
        "## Configuration",
        "",
        f"- Spark: {spark.version}, `{config['spark']['master']}`, driver memory `{config['spark']['driver_memory']}`",
        f"- Integrated metric rows: {validation['integrated_metric_rows']}",
        f"- Warm-up runs: {config['benchmark']['warmup_runs']}",
        f"- Measured runs: {config['benchmark']['measured_runs']}",
        "- Cross-source metrics are aligned by keys and never added across units.",
        "- Missing Ngram years remain null; Getty has no direct movement-term series.",
        "",
        "## Storage results",
        "",
        "| Format | Query | Files read | Bytes read | Total bytes | Median seconds | Output rows |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in storage:
        lines.append(
            f"| {row['format']} | {row['query']} | {row['input_files']} | "
            f"{row['selected_input_bytes']} | {row['storage_bytes']} | "
            f"{row['median_seconds']} | {row['output_rows']} |"
        )
    lines.extend(
        [
            "",
            "## Join strategy",
            "",
            "| Strategy | Median seconds | Output rows |",
            "|---|---:|---:|",
        ]
    )
    for row in joins:
        lines.append(
            f"| {row['join_strategy']} | {row['median_seconds']} | {row['output_rows']} |"
        )
    lines.extend(
        [
            "",
            "Every storage format and both join strategies produced identical row counts and checksums.",
            "Shuffle bytes remain unavailable because PySpark 3.5 has no stable public per-query Python metrics API. HDFS is not configured on this machine.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the comprehensive three-source benchmark")
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "integrated_benchmark.yaml"
    )
    args = parser.parse_args()
    config = load_config(args.config)
    metrics, bridge_pd, validation = build_metrics(config)
    generated = resolve(config["paths"]["generated_data"])
    paths = write_fixtures(metrics, bridge_pd, generated)
    format_files = manifests(paths)
    validation_path = resolve(config["paths"]["validation"])
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    spark = spark_session(config)
    try:
        reference = spark.read.parquet(*[str(path) for path in format_files["parquet_unpartitioned"]])
        if reference.count() != len(metrics):
            raise RuntimeError("Integrated Spark fixture row count mismatch")
        schema = reference.schema
        bridge = spark.read.parquet(str(generated / "artist_movement_bridge.parquet"))
        plans = resolve(config["paths"]["plans"])
        plans.mkdir(parents=True, exist_ok=True)
        warmups = int(config["benchmark"]["warmup_runs"])
        measured = int(config["benchmark"]["measured_runs"])
        rows: list[dict[str, Any]] = []

        for format_name in config["benchmark"]["formats"]:
            for query_name in config["benchmark"]["queries"]:
                for _ in range(warmups):
                    execute_once(
                        spark, format_name, format_files[format_name], schema, query_name, config
                    )
                timings: list[float] = []
                observed: list[tuple[int, str]] = []
                plan = ""
                input_files = 0
                selected_bytes = 0
                for _ in range(measured):
                    elapsed, output_rows, checksum, plan, input_files, selected_bytes = execute_once(
                        spark, format_name, format_files[format_name], schema, query_name, config
                    )
                    timings.append(elapsed)
                    observed.append((output_rows, checksum))
                if len(set(observed)) != 1:
                    raise RuntimeError(f"Unstable integrated output: {format_name}/{query_name}")
                (plans / f"storage_{format_name}_{query_name}.txt").write_text(plan, encoding="utf-8")
                rows.append(
                    {
                        "comparison": "storage_format",
                        "format": format_name,
                        "query": query_name,
                        "join_strategy": "precomputed",
                        "synthetic_benchmark": False,
                        "input_rows": len(metrics),
                        "output_rows": observed[0][0],
                        "checksum": observed[0][1],
                        "storage_bytes": directory_size(paths[format_name]),
                        "input_files": input_files,
                        "selected_input_bytes": selected_bytes,
                        "warmup_runs": warmups,
                        "measured_runs": measured,
                        "run_1_seconds": round(timings[0], 6),
                        "run_2_seconds": round(timings[1], 6),
                        "run_3_seconds": round(timings[2], 6),
                        "median_seconds": round(statistics.median(timings), 6),
                        "spark_version": spark.version,
                        "spark_master": config["spark"]["master"],
                    }
                )

        for broadcast in (False, True):
            for _ in range(warmups):
                execute_join_once(reference, bridge, broadcast)
            timings = []
            observed = []
            plan = ""
            for _ in range(measured):
                elapsed, output_rows, checksum, plan = execute_join_once(reference, bridge, broadcast)
                timings.append(elapsed)
                observed.append((output_rows, checksum))
            if len(set(observed)) != 1:
                raise RuntimeError("Unstable integrated join output")
            strategy = "broadcast" if broadcast else "standard"
            (plans / f"join_{strategy}.txt").write_text(plan, encoding="utf-8")
            rows.append(
                {
                    "comparison": "join_strategy",
                    "format": "parquet_unpartitioned",
                    "query": "movement_artist_sum",
                    "join_strategy": strategy,
                    "synthetic_benchmark": False,
                    "input_rows": len(metrics),
                    "output_rows": observed[0][0],
                    "checksum": observed[0][1],
                    "storage_bytes": directory_size(paths["parquet_unpartitioned"]),
                    "input_files": 1,
                    "selected_input_bytes": directory_size(paths["parquet_unpartitioned"]),
                    "warmup_runs": warmups,
                    "measured_runs": measured,
                    "run_1_seconds": round(timings[0], 6),
                    "run_2_seconds": round(timings[1], 6),
                    "run_3_seconds": round(timings[2], 6),
                    "median_seconds": round(statistics.median(timings), 6),
                    "spark_version": spark.version,
                    "spark_master": config["spark"]["master"],
                }
            )

        validate_results(rows)
        write_results(resolve(config["paths"]["results"]), rows)
        write_summary(resolve(config["paths"]["summary"]), rows, config, validation, spark)
        write_chart(resolve(config["paths"]["chart"]), rows)
        print(json.dumps({"scenarios": len(rows), "input_rows": len(metrics)}, indent=2))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

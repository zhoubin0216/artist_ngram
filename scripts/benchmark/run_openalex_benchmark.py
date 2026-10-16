from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import statistics
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pyspark.sql import DataFrame, SparkSession

from openalex_queries import q4_type_distribution, run_openalex_query
from queries import checksum_frame
from run_benchmark import directory_size, load_config, resolve, spark_session


ROOT = Path(__file__).resolve().parents[2]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_sources(config: dict[str, Any]) -> dict[str, Any]:
    annual_path = resolve(config["paths"]["annual"])
    movement_path = resolve(config["paths"]["movement_annual"])
    type_path = resolve(config["paths"]["type_annual"])
    annual = pd.read_csv(annual_path, dtype={"artist_id": "string"})
    movement = pd.read_csv(movement_path, dtype={"movement_id": "string"})
    types = pd.read_csv(type_path, dtype={"artist_id": "string"})

    if len(annual) != 301_400:
        raise RuntimeError(f"Unexpected OpenAlex annual row count: {len(annual)}")
    if annual.duplicated(["artist_id", "year"]).any():
        raise RuntimeError("OpenAlex annual key artist_id+year is not unique")
    if int(annual["works_count"].sum()) != 15_679:
        raise RuntimeError("OpenAlex annual works_count total is not 15,679")
    if set(annual["year"].unique()) != set(range(1800, 2020)):
        raise RuntimeError("OpenAlex annual year coverage is not 1800-2019")
    if annual["artist_id"].nunique() != 1_370:
        raise RuntimeError("OpenAlex annual artist coverage is not 1,370")
    if len(movement) != 3_300 or movement.duplicated(["movement_id", "year"]).any():
        raise RuntimeError("OpenAlex movement annual keys or row count are invalid")
    identity = (
        movement["artist_linked_works_count"]
        + movement["direct_movement_works_count"]
        - movement["overlap_works_count"]
    )
    if not identity.equals(movement["union_works_count"]):
        raise RuntimeError("OpenAlex movement rows violate U=A+B-O")
    if int(types["works_count"].sum()) != 15_679:
        raise RuntimeError("OpenAlex type-table count total is not 15,679")

    q1 = annual.loc[annual["artist_id"] == "Q296"]
    q2 = annual.loc[annual["year"] == 1900]
    q3 = movement.loc[
        (movement["movement_id"] == "M05")
        & movement["year"].between(1850, 1920)
    ]
    m05_1900 = movement.loc[
        (movement["movement_id"] == "M05") & (movement["year"] == 1900)
    ].iloc[0]
    validation = {
        "source_files": {
            "annual": str(annual_path.relative_to(ROOT)),
            "movement_annual": str(movement_path.relative_to(ROOT)),
            "type_annual": str(type_path.relative_to(ROOT)),
        },
        "source_sha256": {
            "annual": file_sha256(annual_path),
            "movement_annual": file_sha256(movement_path),
            "type_annual": file_sha256(type_path),
        },
        "annual_rows": len(annual),
        "annual_artists": int(annual["artist_id"].nunique()),
        "annual_works_count_sum": int(annual["works_count"].sum()),
        "q1_q296_rows": len(q1),
        "q1_q296_works_count_sum": int(q1["works_count"].sum()),
        "q2_1900_rows": len(q2),
        "q3_m05_1850_1920_rows": len(q3),
        "q3_m05_1900": {
            "artist_linked_works_count": int(m05_1900["artist_linked_works_count"]),
            "direct_movement_works_count": int(m05_1900["direct_movement_works_count"]),
            "overlap_works_count": int(m05_1900["overlap_works_count"]),
            "union_works_count": int(m05_1900["union_works_count"]),
        },
        "q4_type_rows": len(types),
        "q4_artist_work_match_total": int(types["works_count"].sum()),
        "q4_distinct_work_total": None,
        "q4_distinct_work_note": (
            "The annual type table is already aggregated by artist. A global distinct "
            "Work-ID total requires results/openalex/artist_work_clean.jsonl and is not "
            "inferred from these counts."
        ),
    }
    output_path = resolve(config["paths"]["validation"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return validation


def write_validation(config: dict[str, Any], validation: dict[str, Any]) -> None:
    output_path = resolve(config["paths"]["validation"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_formats(source_path: Path, base: Path) -> tuple[dict[str, Path], int]:
    annual = pd.read_csv(source_path, dtype={"artist_id": "string"})
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
    annual.to_csv(paths["csv"] / "annual.csv", index=False)
    annual.to_json(
        paths["json"] / "annual.jsonl",
        orient="records",
        lines=True,
        force_ascii=False,
    )
    annual.to_parquet(paths["parquet_unpartitioned"] / "annual.parquet", index=False)
    arrow_schema = pa.Table.from_pandas(annual, preserve_index=False).schema
    for year, frame in annual.groupby("year", sort=True):
        partition_dir = paths["parquet_partitioned"] / f"year={int(year)}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pandas(
                frame, schema=arrow_schema, preserve_index=False, safe=True
            ),
            partition_dir / "annual.parquet",
        )
    return paths, len(annual)


def build_manifests(paths: dict[str, Path]) -> dict[str, list[Path]]:
    patterns = {"csv": "*.csv", "json": "*.jsonl"}
    manifests: dict[str, list[Path]] = {}
    for name, path in paths.items():
        manifests[name] = sorted(path.rglob(patterns.get(name, "*.parquet")))
        if not manifests[name]:
            raise RuntimeError(f"No files found for {name}: {path}")
    return manifests


def select_files(
    format_name: str, files: list[Path], query_name: str, snapshot_year: int
) -> list[Path]:
    if format_name != "parquet_partitioned" or query_name != "q2":
        return files
    selected = [path for path in files if path.parent.name == f"year={snapshot_year}"]
    if not selected:
        raise RuntimeError(f"No partition found for year {snapshot_year}")
    return selected


def read_format(
    spark: SparkSession, name: str, files: list[Path], schema
) -> DataFrame:
    names = [str(path) for path in files]
    if name == "csv":
        return spark.read.option("header", True).schema(schema).csv(names)
    if name == "json":
        return spark.read.schema(schema).json(names)
    return spark.read.parquet(*names)


def execute_once(
    spark: SparkSession,
    format_name: str,
    format_files: list[Path],
    schema,
    artists: DataFrame,
    query_name: str,
    config: dict[str, Any],
    broadcast_join: bool,
) -> tuple[float, int, str, str, int, int]:
    selected = select_files(
        format_name,
        format_files,
        query_name,
        int(config["benchmark"]["snapshot_year"]),
    )
    started = time.perf_counter()
    annual = read_format(spark, format_name, selected, schema)
    result = run_openalex_query(
        query_name,
        annual,
        artists,
        artist_id=config["benchmark"]["artist_id"],
        artist_year_start=int(config["benchmark"]["artist_year_start"]),
        artist_year_end=int(config["benchmark"]["artist_year_end"]),
        snapshot_year=int(config["benchmark"]["snapshot_year"]),
        broadcast_join=broadcast_join,
    )
    plan = result._jdf.queryExecution().executedPlan().toString()
    output_rows, checksum = checksum_frame(result)
    elapsed = time.perf_counter() - started
    return (
        elapsed,
        output_rows,
        checksum,
        plan,
        len(selected),
        sum(path.stat().st_size for path in selected),
    )


def validate_results(rows: list[dict[str, Any]]) -> None:
    storage = [row for row in rows if row["comparison"] == "storage_format"]
    for query_name in sorted({row["query"] for row in storage}):
        observed = {
            (row["output_rows"], row["checksum"])
            for row in storage
            if row["query"] == query_name
        }
        if len(observed) != 1:
            raise RuntimeError(f"Result mismatch across formats for {query_name}: {observed}")
    joins = [row for row in rows if row["comparison"] == "join_strategy"]
    if len({(row["output_rows"], row["checksum"]) for row in joins}) != 1:
        raise RuntimeError("Result mismatch between standard and broadcast joins")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_chart(path: Path, rows: list[dict[str, Any]]) -> None:
    storage = [row for row in rows if row["comparison"] == "storage_format"]
    labels = [f"{row['format']}\n{row['query'].upper()}" for row in storage]
    values = [float(row["median_seconds"]) for row in storage]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(range(len(values)), values, color="#447A5E")
    ax.set_ylabel("Median wall time (seconds)")
    ax.set_title("OpenAlex real-data Spark benchmark")
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
    validation: dict[str, Any],
) -> None:
    storage = [row for row in rows if row["comparison"] == "storage_format"]
    joins = [row for row in rows if row["comparison"] == "join_strategy"]
    lookup = {(row["format"], row["query"]): row for row in storage}
    csv_bytes = int(lookup[("csv", "q1")]["storage_bytes"])
    parquet_bytes = int(lookup[("parquet_unpartitioned", "q1")]["storage_bytes"])
    partitioned = float(lookup[("parquet_partitioned", "q2")]["median_seconds"])
    unpartitioned = float(lookup[("parquet_unpartitioned", "q2")]["median_seconds"])
    standard = float(next(row["median_seconds"] for row in joins if row["join_strategy"] == "standard"))
    broadcast = float(next(row["median_seconds"] for row in joins if row["join_strategy"] == "broadcast"))
    lines = [
        "# OpenAlex benchmark summary",
        "",
        "All timings were measured on this Windows computer. API and fixture-generation time is excluded.",
        "The input is the real 301,400-row OpenAlex annual table; no synthetic duplication is used.",
        "",
        "## Fixed configuration",
        "",
        f"- Spark version: {spark.version}",
        f"- Master: `{config['spark']['master']}`",
        f"- Driver memory: `{config['spark']['driver_memory']}`",
        f"- Shuffle partitions: `{config['spark']['shuffle_partitions']}`",
        f"- Adaptive execution: `{config['spark']['adaptive_enabled']}`",
        f"- Warm-up runs: `{config['benchmark']['warmup_runs']}`",
        f"- Measured runs: `{config['benchmark']['measured_runs']}`",
        "",
        "## Validation snapshot",
        "",
        f"- Annual rows: {validation['annual_rows']}; artists: {validation['annual_artists']}; retained artist-work matches: {validation['annual_works_count_sum']}.",
        f"- Q1 Monet rows: {validation['q1_q296_rows']}; Q2 1900 rows: {validation['q2_1900_rows']}; M05 1850-1920 rows: {validation['q3_m05_1850_1920_rows']}.",
        "- The type table sums to 15,679 artist-work matches. A global distinct Work-ID count is deliberately left unavailable because the aggregate table cannot support it.",
        "",
        "## Findings",
        "",
        f"- Unpartitioned Parquet used {100 * (1 - parquet_bytes / csv_bytes):.1f}% less disk space than CSV.",
        f"- For Q2 (year {config['benchmark']['snapshot_year']}), year-partitioned Parquet was {100 * (1 - partitioned / unpartitioned):.1f}% faster than unpartitioned Parquet.",
        f"- Broadcast join was {100 * (broadcast / standard - 1):.1f}% {'slower' if broadcast >= standard else 'faster'} than the standard join on this machine.",
        "- Q3 is an artist-membership sum for engineering comparison, not the official Work-ID-deduplicated OpenAlex movement series.",
        "",
        "## Storage formats",
        "",
        "| Format | Query | Input rows | Files read | Bytes read | Total storage bytes | Median seconds | Output rows | Validated |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in storage:
        lines.append(
            f"| {row['format']} | {row['query'].upper()} | {row['input_rows']} | "
            f"{row['input_files']} | {row['selected_input_bytes']} | {row['storage_bytes']} | "
            f"{row['median_seconds']} | {row['output_rows']} | yes |"
        )
    lines.extend([
        "",
        "## Join strategies",
        "",
        "| Strategy | Query | Median seconds | Output rows | Validated |",
        "|---|---:|---:|---:|---|",
    ])
    for row in joins:
        lines.append(
            f"| {row['join_strategy']} | {row['query'].upper()} | {row['median_seconds']} | {row['output_rows']} | yes |"
        )
    lines.extend([
        "",
        "Shuffle read/write bytes are blank in the CSV because PySpark 3.5 does not expose stable per-query task metrics through a public Python API. Executed plans are stored in the ignored benchmark plan directory.",
        "HDFS was not tested because it is not configured on this machine.",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark OpenAlex annual data with Spark")
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "openalex_benchmark.yaml"
    )
    args = parser.parse_args()
    config = load_config(args.config)
    validation = validate_sources(config)
    paths, input_rows = write_formats(
        resolve(config["paths"]["annual"]), resolve(config["paths"]["generated_data"])
    )
    manifests = build_manifests(paths)
    spark = spark_session(config)
    try:
        artists = spark.read.option("header", True).option("inferSchema", True).csv(
            str(resolve(config["paths"]["artist_dictionary"]))
        )
        reference = spark.read.parquet(*[str(p) for p in manifests["parquet_unpartitioned"]])
        schema = reference.schema
        if reference.count() != input_rows:
            raise RuntimeError("Spark fixture row count differs from source row count")
        type_annual = (
            spark.read.option("header", True)
            .option("inferSchema", True)
            .csv(str(resolve(config["paths"]["type_annual"])))
        )
        q4 = q4_type_distribution(type_annual, 1800, 2019)
        q4_rows, q4_checksum = checksum_frame(q4)
        validation["q4_spark_output_rows"] = q4_rows
        validation["q4_spark_checksum"] = q4_checksum
        write_validation(config, validation)
        plans = resolve(config["paths"]["plans"])
        plans.mkdir(parents=True, exist_ok=True)
        warmups = int(config["benchmark"]["warmup_runs"])
        measured = int(config["benchmark"]["measured_runs"])
        scenarios: list[tuple[str, str, str, bool]] = []
        for format_name in config["benchmark"]["formats"]:
            for query_name in config["benchmark"]["queries"]:
                scenarios.append(("storage_format", format_name, query_name, False))
        scenarios.extend([
            ("join_strategy", "parquet_unpartitioned", "q3", False),
            ("join_strategy", "parquet_unpartitioned", "q3", True),
        ])
        rows: list[dict[str, Any]] = []
        for comparison, format_name, query_name, broadcast_join in scenarios:
            for _ in range(warmups):
                execute_once(
                    spark, format_name, manifests[format_name], schema, artists,
                    query_name, config, broadcast_join,
                )
            timings: list[float] = []
            observed: list[tuple[int, str]] = []
            plan = ""
            input_files = 0
            selected_bytes = 0
            for _ in range(measured):
                elapsed, output_rows, checksum, plan, input_files, selected_bytes = execute_once(
                    spark, format_name, manifests[format_name], schema, artists,
                    query_name, config, broadcast_join,
                )
                timings.append(elapsed)
                observed.append((output_rows, checksum))
            if len(set(observed)) != 1:
                raise RuntimeError(f"Unstable output for {format_name}/{query_name}")
            strategy = "broadcast" if broadcast_join else "standard"
            (plans / f"{comparison}_{format_name}_{query_name}_{strategy}.txt").write_text(
                plan, encoding="utf-8"
            )
            rows.append({
                "comparison": comparison,
                "format": format_name,
                "query": query_name,
                "join_strategy": strategy,
                "synthetic_benchmark": False,
                "input_rows": input_rows,
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
                "shuffle_read_bytes": "",
                "shuffle_write_bytes": "",
                "spark_version": spark.version,
                "spark_master": config["spark"]["master"],
                "spark_driver_memory": config["spark"]["driver_memory"],
                "spark_shuffle_partitions": config["spark"]["shuffle_partitions"],
            })
        validate_results(rows)
        write_csv(resolve(config["paths"]["results"]), rows)
        write_summary(resolve(config["paths"]["summary"]), rows, config, spark, validation)
        write_chart(resolve(config["paths"]["chart"]), rows)
        print(json.dumps({"scenarios": len(rows), "input_rows": input_rows}, indent=2))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

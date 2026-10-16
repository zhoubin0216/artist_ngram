from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_integrated_metrics import ROOT, build_metrics, resolve, write_fixtures
from integrated_queries import query_outputs, select_query
from queries import checksum_frame
from run_benchmark import load_config, spark_session


def main() -> None:
    parser = argparse.ArgumentParser(description="Query Ngram, OpenAlex, Getty, and their aligned summary")
    parser.add_argument("query", choices=["artist", "movement", "period"])
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "integrated_benchmark.yaml")
    parser.add_argument("--artist-id", default=None)
    parser.add_argument("--movement-id", default=None)
    parser.add_argument("--year-start", type=int, default=None)
    parser.add_argument("--year-end", type=int, default=None)
    parser.add_argument("--label", default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    benchmark = config["benchmark"]
    if args.artist_id:
        benchmark["artist_id"] = args.artist_id
    if args.movement_id:
        benchmark["movement_id"] = args.movement_id
    if args.year_start is not None:
        benchmark[f"{args.query}_year_start" if args.query != "period" else "period_year_start"] = args.year_start
    if args.year_end is not None:
        benchmark[f"{args.query}_year_end" if args.query != "period" else "period_year_end"] = args.year_end

    generated = resolve(config["paths"]["generated_data"])
    parquet_path = generated / "parquet_unpartitioned" / "metrics.parquet"
    if not parquet_path.exists():
        metrics_pd, bridge_pd, _ = build_metrics(config)
        write_fixtures(metrics_pd, bridge_pd, generated)

    spark = spark_session(config)
    try:
        metrics = spark.read.parquet(str(parquet_path))
        selected = select_query(
            metrics,
            args.query,
            artist_id=benchmark["artist_id"],
            movement_id=benchmark["movement_id"],
            artist_year_start=int(benchmark["artist_year_start"]),
            artist_year_end=int(benchmark["artist_year_end"]),
            movement_year_start=int(benchmark["movement_year_start"]),
            movement_year_end=int(benchmark["movement_year_end"]),
            period_year_start=int(benchmark["period_year_start"]),
            period_year_end=int(benchmark["period_year_end"]),
        )
        outputs = query_outputs(selected)
        label = args.label or args.query
        output_dir = resolve(config["paths"]["query_results"]) / label
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"query": args.query, "label": label, "outputs": {}}
        for name, frame in outputs.items():
            rows, checksum = checksum_frame(frame)
            path = output_dir / f"{name}.csv"
            frame.toPandas().to_csv(path, index=False)
            manifest["outputs"][name] = {
                "rows": rows,
                "checksum": checksum,
                "path": str(path.relative_to(ROOT)),
            }
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

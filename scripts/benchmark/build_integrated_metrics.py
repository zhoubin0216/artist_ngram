from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
STANDARD_COLUMNS = [
    "source",
    "entity_type",
    "entity_id",
    "entity_name",
    "year",
    "series_kind",
    "metric",
    "value",
    "query_term",
    "n",
    "availability",
]


def resolve(path: str) -> Path:
    return (ROOT / path).resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metric_rows(
    frame: pd.DataFrame,
    *,
    source: str,
    entity_type: str,
    id_column: str,
    name_column: str,
    series_kind: str,
    metric_columns: list[str],
    query_column: str | None = None,
    n_column: str | None = None,
    availability_column: str | None = None,
) -> pd.DataFrame:
    identifiers = [id_column, name_column, "year"]
    for optional in (query_column, n_column, availability_column):
        if optional and optional not in identifiers:
            identifiers.append(optional)
    melted = frame.melt(
        id_vars=identifiers,
        value_vars=metric_columns,
        var_name="metric",
        value_name="value",
    )
    result = pd.DataFrame(
        {
            "source": source,
            "entity_type": entity_type,
            "entity_id": melted[id_column].astype("string"),
            "entity_name": melted[name_column].astype("string"),
            "year": pd.to_numeric(melted["year"], errors="raise").astype("int64"),
            "series_kind": series_kind,
            "metric": melted["metric"].astype("string"),
            "value": pd.to_numeric(melted["value"], errors="coerce").astype("float64"),
            "query_term": (
                melted[query_column].astype("string") if query_column else pd.NA
            ),
            "n": (
                pd.to_numeric(melted[n_column], errors="coerce").astype("Int64")
                if n_column
                else pd.Series(pd.NA, index=melted.index, dtype="Int64")
            ),
            "availability": (
                melted[availability_column].astype("string")
                if availability_column
                else "observed"
            ),
        }
    )
    return result[STANDARD_COLUMNS]


def build_bridge(dictionary: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for item in dictionary.itertuples(index=False):
        ids = [part.strip() for part in str(item.movement_ids or "").split(";") if part.strip()]
        names = [part.strip() for part in str(item.movement_names or "").split(";") if part.strip()]
        if len(ids) != len(names):
            raise RuntimeError(f"Movement ID/name mismatch for {item.artist_id}")
        for movement_id, movement_name in zip(ids, names):
            rows.append(
                {
                    "artist_id": str(item.artist_id),
                    "movement_id": movement_id,
                    "movement_name": movement_name,
                }
            )
    bridge = pd.DataFrame(rows).drop_duplicates()
    if bridge.duplicated(["artist_id", "movement_id"]).any():
        raise RuntimeError("Artist-movement bridge key is not unique")
    return bridge.sort_values(["artist_id", "movement_id"]).reset_index(drop=True)


def build_metrics(config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    paths = config["paths"]
    dictionary_path = resolve(paths["artist_dictionary"])
    ngram_artist_path = resolve(paths["ngram_artist"])
    ngram_movement_path = resolve(paths["ngram_movement"])
    ngram_movements_path = resolve(paths["ngram_movements"])
    openalex_artist_path = resolve(paths["openalex_artist"])
    openalex_movement_path = resolve(paths["openalex_movement"])
    getty_path = resolve(paths["getty_artist_year"])

    dictionary = pd.read_csv(dictionary_path, dtype={"artist_id": "string"})
    bridge = build_bridge(dictionary)
    artist_names = dictionary[["artist_id", "canonical_name"]]

    ngram_artist = pd.read_json(ngram_artist_path, lines=True)
    ngram_artist = ngram_artist.merge(artist_names, on="artist_id", how="left", validate="many_to_one")
    ngram_artist_metrics = metric_rows(
        ngram_artist,
        source="ngram",
        entity_type="artist",
        id_column="artist_id",
        name_column="canonical_name",
        series_kind="artist_observed",
        metric_columns=["match_count", "volume_count"],
        query_column="query",
        n_column="n",
    )

    openalex_artist = pd.read_csv(openalex_artist_path, dtype={"artist_id": "string"})
    openalex_artist_metrics = metric_rows(
        openalex_artist,
        source="openalex",
        entity_type="artist",
        id_column="artist_id",
        name_column="artist_name",
        series_kind="artist_complete_grid",
        metric_columns=[
            "works_count",
            "analysis_works_count",
            "denominator_works",
            "works_per_10000",
        ],
        availability_column="normalization_status",
    )

    getty = pd.read_csv(getty_path, dtype={"artist_id": "string"}).rename(
        columns={"event_year": "year"}
    )
    getty = getty.merge(artist_names, on="artist_id", how="left", validate="many_to_one")
    getty_metrics = metric_rows(
        getty,
        source="getty",
        entity_type="artist",
        id_column="artist_id",
        name_column="canonical_name",
        series_kind="artist_observed",
        metric_columns=[
            "confirmed_sale_count",
            "catalog_entry_count",
            "total_record_count",
        ],
    )

    movement_names = {
        row["movement_id"]: row.get("dictionary_label") or row.get("query")
        for row in json.loads(ngram_movements_path.read_text(encoding="utf-8"))
    }

    ngram_member = ngram_artist.merge(bridge, on="artist_id", how="inner", validate="many_to_many")
    ngram_member = (
        ngram_member.groupby(["movement_id", "movement_name", "year"], as_index=False)[
            ["match_count", "volume_count"]
        ]
        .sum()
    )
    ngram_member_metrics = metric_rows(
        ngram_member,
        source="ngram",
        entity_type="movement",
        id_column="movement_id",
        name_column="movement_name",
        series_kind="artist_membership_sum",
        metric_columns=["match_count", "volume_count"],
    )

    openalex_member = openalex_artist.merge(
        bridge, on="artist_id", how="inner", validate="many_to_many"
    )
    grouped_oa = openalex_member.groupby(
        ["movement_id", "movement_name", "year"], as_index=False
    ).agg(
        works_count=("works_count", "sum"),
        analysis_works_count=("analysis_works_count", "sum"),
        denominator_works=("denominator_works", "first"),
    )
    grouped_oa["works_per_10000"] = (
        grouped_oa["analysis_works_count"]
        / grouped_oa["denominator_works"].where(grouped_oa["denominator_works"] != 0)
        * 10_000
    )
    openalex_member_metrics = metric_rows(
        grouped_oa,
        source="openalex",
        entity_type="movement",
        id_column="movement_id",
        name_column="movement_name",
        series_kind="artist_membership_sum",
        metric_columns=[
            "works_count",
            "analysis_works_count",
            "denominator_works",
            "works_per_10000",
        ],
    )

    getty_member = getty.merge(bridge, on="artist_id", how="inner", validate="many_to_many")
    getty_member = (
        getty_member.groupby(["movement_id", "movement_name", "year"], as_index=False)[
            ["confirmed_sale_count", "catalog_entry_count", "total_record_count"]
        ]
        .sum()
    )
    getty_member_metrics = metric_rows(
        getty_member,
        source="getty",
        entity_type="movement",
        id_column="movement_id",
        name_column="movement_name",
        series_kind="artist_membership_sum",
        metric_columns=[
            "confirmed_sale_count",
            "catalog_entry_count",
            "total_record_count",
        ],
    )

    ngram_movement = pd.read_json(ngram_movement_path, lines=True)
    ngram_movement["movement_name"] = ngram_movement["movement_id"].map(movement_names)
    ngram_direct_metrics = metric_rows(
        ngram_movement,
        source="ngram",
        entity_type="movement",
        id_column="movement_id",
        name_column="movement_name",
        series_kind="direct_movement_term",
        metric_columns=["match_count", "volume_count"],
        query_column="query",
        n_column="n",
    )

    openalex_movement = pd.read_csv(openalex_movement_path, dtype={"movement_id": "string"})
    openalex_direct_metrics = metric_rows(
        openalex_movement,
        source="openalex",
        entity_type="movement",
        id_column="movement_id",
        name_column="movement_name",
        series_kind="direct_official_union",
        metric_columns=[
            "artist_linked_works_count",
            "direct_movement_works_count",
            "overlap_works_count",
            "union_works_count",
            "denominator_works",
            "movement_works_per_10000",
        ],
        availability_column="normalization_status",
    )

    metrics = pd.concat(
        [
            ngram_artist_metrics,
            openalex_artist_metrics,
            getty_metrics,
            ngram_member_metrics,
            openalex_member_metrics,
            getty_member_metrics,
            ngram_direct_metrics,
            openalex_direct_metrics,
        ],
        ignore_index=True,
    )
    metrics = metrics.loc[metrics["year"].between(1800, 2019)].copy()
    key = ["source", "entity_type", "entity_id", "year", "series_kind", "metric"]
    if metrics.duplicated(key).any():
        sample = metrics.loc[metrics.duplicated(key, keep=False), key].head().to_dict("records")
        raise RuntimeError(f"Integrated metric key is not unique: {sample}")
    metrics = metrics.sort_values(key).reset_index(drop=True)

    validation = {
        "source_files": {
            name: str(path.relative_to(ROOT))
            for name, path in {
                "artist_dictionary": dictionary_path,
                "ngram_artist": ngram_artist_path,
                "ngram_movement": ngram_movement_path,
                "openalex_artist": openalex_artist_path,
                "openalex_movement": openalex_movement_path,
                "getty_artist_year": getty_path,
            }.items()
        },
        "source_sha256": {
            name: sha256(path)
            for name, path in {
                "artist_dictionary": dictionary_path,
                "ngram_artist": ngram_artist_path,
                "ngram_movement": ngram_movement_path,
                "openalex_artist": openalex_artist_path,
                "openalex_movement": openalex_movement_path,
                "getty_artist_year": getty_path,
            }.items()
        },
        "artist_dictionary_rows": len(dictionary),
        "artist_movement_bridge_rows": len(bridge),
        "integrated_metric_rows": len(metrics),
        "rows_by_source": {
            str(key): int(value)
            for key, value in metrics.groupby("source").size().to_dict().items()
        },
        "rows_by_entity_type": {
            str(key): int(value)
            for key, value in metrics.groupby("entity_type").size().to_dict().items()
        },
        "year_min": int(metrics["year"].min()),
        "year_max": int(metrics["year"].max()),
        "missing_values_are_not_zero_filled": True,
        "cross_source_values_are_not_summed": True,
        "getty_direct_movement_available": False,
    }
    return metrics, bridge, validation


def write_fixtures(
    metrics: pd.DataFrame, bridge: pd.DataFrame, base: Path
) -> dict[str, Path]:
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
    metrics.to_csv(paths["csv"] / "metrics.csv", index=False)
    metrics.to_json(
        paths["json"] / "metrics.jsonl",
        orient="records",
        lines=True,
        force_ascii=False,
    )
    metrics.to_parquet(paths["parquet_unpartitioned"] / "metrics.parquet", index=False)
    arrow_schema = pa.Table.from_pandas(metrics, preserve_index=False).schema
    for year, frame in metrics.groupby("year", sort=True):
        folder = paths["parquet_partitioned"] / f"year={int(year)}"
        folder.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pandas(frame, schema=arrow_schema, preserve_index=False, safe=True),
            folder / "metrics.parquet",
        )
    bridge.to_parquet(base / "artist_movement_bridge.parquet", index=False)
    bridge.to_csv(base / "artist_movement_bridge.csv", index=False)
    return paths

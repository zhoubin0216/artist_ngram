from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


OUTPUT_COLUMNS = [
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


def select_query(
    metrics: DataFrame,
    query_name: str,
    *,
    artist_id: str,
    movement_id: str,
    artist_year_start: int,
    artist_year_end: int,
    movement_year_start: int,
    movement_year_end: int,
    period_year_start: int,
    period_year_end: int,
) -> DataFrame:
    if query_name == "artist":
        return metrics.filter(
            (F.col("entity_type") == "artist")
            & (F.col("entity_id") == artist_id)
            & F.col("year").between(artist_year_start, artist_year_end)
        )
    if query_name == "movement":
        return metrics.filter(
            (F.col("entity_type") == "movement")
            & (F.col("entity_id") == movement_id)
            & F.col("year").between(movement_year_start, movement_year_end)
        )
    if query_name == "period":
        return metrics.filter(F.col("year").between(period_year_start, period_year_end))
    raise ValueError(f"Unknown integrated query: {query_name}")


def summary_wide(selected: DataFrame) -> DataFrame:
    labelled = (
        selected.withColumn(
            "series_group",
            F.when(F.col("entity_type") == "artist", F.lit("artist"))
            .when(
                F.col("series_kind") == "artist_membership_sum",
                F.lit("artist_membership_sum"),
            )
            .otherwise(F.lit("direct_movement")),
        )
        .withColumn("source_metric", F.concat_ws("__", "source", "metric"))
    )
    keys = ["entity_type", "entity_id", "year", "series_group"]
    names = labelled.groupBy(*keys).agg(F.min("entity_name").alias("entity_name"))
    values = (
        labelled.groupBy(*keys)
        .pivot("source_metric")
        .agg(F.first("value", ignorenulls=True))
    )
    return (
        names.join(values, keys, "inner")
        .select(
            "entity_type",
            "entity_id",
            "entity_name",
            "year",
            "series_group",
            *sorted(column for column in values.columns if column not in keys),
        )
        .orderBy("entity_type", "entity_id", "year", "series_group")
    )


def query_outputs(selected: DataFrame) -> dict[str, DataFrame]:
    outputs = {
        source: selected.filter(F.col("source") == source)
        .select(*OUTPUT_COLUMNS)
        .orderBy("entity_type", "entity_id", "year", "series_kind", "metric")
        for source in ("ngram", "openalex", "getty")
    }
    outputs["summary"] = summary_wide(selected)
    return outputs


def movement_join_aggregate(
    metrics: DataFrame, bridge: DataFrame, *, broadcast_join: bool
) -> DataFrame:
    additive_metrics = (
        metrics.filter(F.col("entity_type") == "artist")
        .filter(
            ((F.col("source") == "ngram") & F.col("metric").isin("match_count", "volume_count"))
            | ((F.col("source") == "openalex") & F.col("metric").isin("works_count", "analysis_works_count"))
            | ((F.col("source") == "getty") & F.col("metric").isin("confirmed_sale_count", "catalog_entry_count", "total_record_count"))
        )
        .select("source", "entity_id", "year", "metric", "value")
    )
    dimension = bridge.select("artist_id", "movement_id", "movement_name")
    if broadcast_join:
        dimension = F.broadcast(dimension)
    return (
        additive_metrics.join(
            dimension, additive_metrics.entity_id == dimension.artist_id, "inner"
        )
        .groupBy("source", "movement_id", "movement_name", "year", "metric")
        .agg(F.sum("value").alias("value"))
        .orderBy("source", "movement_id", "year", "metric")
    )

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, FloatType


def _artist_dimension(artists: DataFrame, broadcast_join: bool) -> DataFrame:
    selected = artists.select("artist_id", "canonical_name", "movement_names")
    return F.broadcast(selected) if broadcast_join else selected


def q1_artist_year(events: DataFrame, artist_id: str) -> DataFrame:
    return (
        events.filter(F.col("artist_id") == artist_id)
        .groupBy("artist_id", "event_year", "event_type")
        .agg(F.countDistinct("event_id").alias("record_count"))
        .orderBy("artist_id", "event_year", "event_type")
    )


def q2_year_range(events: DataFrame, year_start: int, year_end: int) -> DataFrame:
    return events.filter(F.col("event_year").between(year_start, year_end)).select(
        "artist_id",
        "object_id",
        "event_id",
        "event_year",
        "event_type",
        "transaction_status",
        "price_value",
        "currency",
    )


def q3_movement_year(
    events: DataFrame, artists: DataFrame, *, broadcast_join: bool
) -> DataFrame:
    dimension = _artist_dimension(artists, broadcast_join)
    joined = events.join(dimension, "artist_id", "inner")
    return (
        joined.withColumn("movement", F.explode(F.split("movement_names", r";\s*")))
        .filter(F.length(F.trim("movement")) > 0)
        .groupBy("movement", "event_year")
        .agg(F.countDistinct("event_id").alias("record_count"))
        .orderBy("movement", "event_year")
    )


def q4_sales_vs_catalog(events: DataFrame) -> DataFrame:
    return (
        events.groupBy("artist_id", "event_year")
        .agg(
            F.sum(
                F.when(F.col("event_type") == "confirmed_sale", 1).otherwise(0)
            ).alias("confirmed_sale_count"),
            F.sum(
                F.when(F.col("event_type") == "catalog_entry", 1).otherwise(0)
            ).alias("catalog_entry_count"),
        )
        .orderBy("artist_id", "event_year")
    )


def run_query(
    query_name: str,
    events: DataFrame,
    artists: DataFrame,
    *,
    artist_id: str,
    year_start: int,
    year_end: int,
    broadcast_join: bool,
) -> DataFrame:
    if query_name == "q1":
        return q1_artist_year(events, artist_id)
    if query_name == "q2":
        return q2_year_range(events, year_start, year_end)
    if query_name == "q3":
        return q3_movement_year(events, artists, broadcast_join=broadcast_join)
    if query_name == "q4":
        return q4_sales_vs_catalog(events)
    raise ValueError(f"Unknown query: {query_name}")


def checksum_frame(frame: DataFrame) -> tuple[int, str]:
    columns = sorted(frame.columns)
    fields = {field.name: field for field in frame.schema.fields}

    def canonical_value(column: str):
        value = F.col(column)
        if isinstance(fields[column].dataType, (DoubleType, FloatType)):
            # CSV/JSON and Parquet can decode the same decimal price to doubles
            # that differ only below their source precision. Getty prices in this
            # pilot have at most two decimals; eight places is conservative.
            value = F.bround(value, 8)
        return F.coalesce(value.cast("string"), F.lit("<NULL>"))

    canonical = F.concat_ws(
        "\u001f",
        *[canonical_value(column) for column in columns],
    )
    digest = F.sha2(canonical, 256)
    row = frame.select(digest.alias("digest")).agg(
        F.count("*").alias("row_count"),
        F.min("digest").alias("min_digest"),
        F.max("digest").alias("max_digest"),
        F.sum(F.crc32("digest").cast("decimal(38,0)")).alias("digest_sum"),
    ).first()
    checksum = f"{row['min_digest']}:{row['max_digest']}:{row['digest_sum']}"
    return int(row["row_count"]), checksum

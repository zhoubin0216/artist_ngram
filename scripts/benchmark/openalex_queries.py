from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


def q1_artist_series(
    annual: DataFrame, artist_id: str, year_start: int, year_end: int
) -> DataFrame:
    """Return one artist's annual raw and normalized OpenAlex measures."""
    return (
        annual.filter(
            (F.col("artist_id") == artist_id)
            & F.col("year").between(year_start, year_end)
        )
        .select(
            "artist_id",
            "artist_name",
            "year",
            "works_count",
            "denominator_works",
            "works_per_10000",
            "normalization_status",
        )
        .orderBy("year")
    )


def q2_year_snapshot(annual: DataFrame, year: int) -> DataFrame:
    """Return all artists for one year, ordered by raw visibility."""
    return (
        annual.filter(F.col("year") == year)
        .select(
            "artist_id",
            "artist_name",
            "year",
            "works_count",
            "denominator_works",
            "works_per_10000",
        )
        .orderBy(F.desc("works_count"), "artist_id")
    )


def _artist_movement_dimension(
    artists: DataFrame, broadcast_join: bool
) -> DataFrame:
    dimension = (
        artists.select(
            "artist_id",
            F.explode(F.split(F.coalesce("movement_ids", F.lit("")), r";\s*")).alias(
                "movement_id"
            ),
        )
        .withColumn("movement_id", F.trim("movement_id"))
        .filter(F.length("movement_id") > 0)
        .dropDuplicates(["artist_id", "movement_id"])
    )
    return F.broadcast(dimension) if broadcast_join else dimension


def q3_movement_year_membership_sum(
    annual: DataFrame, artists: DataFrame, *, broadcast_join: bool
) -> DataFrame:
    """Aggregate artist annual metrics by dictionary movement membership.

    The count is an artist-membership sum. It is intentionally not labelled as
    the official OpenAlex movement Work-ID union, which is supplied separately.
    """
    dimension = _artist_movement_dimension(artists, broadcast_join)
    return (
        annual.join(dimension, "artist_id", "inner")
        .groupBy("movement_id", "year")
        .agg(
            F.sum("works_count").alias("artist_membership_works_sum"),
            F.sum("analysis_works_count").alias("analysis_membership_works_sum"),
            F.countDistinct("artist_id").alias("artist_count"),
        )
        .orderBy("movement_id", "year")
    )


def q4_type_distribution(
    type_annual: DataFrame, year_start: int, year_end: int
) -> DataFrame:
    """Sum artist-work matches by OpenAlex type for a fixed year range.

    This aggregate input cannot recover a global distinct Work-ID count, so the
    output names the supported unit explicitly.
    """
    return (
        type_annual.filter(F.col("year").between(year_start, year_end))
        .groupBy("type")
        .agg(F.sum("works_count").alias("artist_work_match_count"))
        .orderBy(F.desc("artist_work_match_count"), "type")
    )


def run_openalex_query(
    query_name: str,
    annual: DataFrame,
    artists: DataFrame,
    *,
    artist_id: str,
    artist_year_start: int,
    artist_year_end: int,
    snapshot_year: int,
    broadcast_join: bool,
) -> DataFrame:
    if query_name == "q1":
        return q1_artist_series(
            annual, artist_id, artist_year_start, artist_year_end
        )
    if query_name == "q2":
        return q2_year_snapshot(annual, snapshot_year)
    if query_name == "q3":
        return q3_movement_year_membership_sum(
            annual, artists, broadcast_join=broadcast_join
        )
    raise ValueError(f"Unknown OpenAlex query: {query_name}")

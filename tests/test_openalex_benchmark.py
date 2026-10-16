from __future__ import annotations

import os
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")
from pyspark.sql import SparkSession

from openalex_queries import (
    q1_artist_series,
    q2_year_snapshot,
    q3_movement_year_membership_sum,
    q4_type_distribution,
)
from queries import checksum_frame
from windows_spark import prepare_windows_spark_runtime


@pytest.fixture(scope="module")
def spark() -> SparkSession:
    prepare_windows_spark_runtime()
    java_home = Path(r"C:\Program Files\Java\jre1.8.0_501")
    if java_home.exists():
        os.environ["JAVA_HOME"] = str(java_home)
        os.environ["PATH"] = f"{java_home / 'bin'};{os.environ.get('PATH', '')}"
    session = (
        SparkSession.builder.master("local[2]")
        .appName("openalex-benchmark-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.autoBroadcastJoinThreshold", "-1")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )
    yield session
    session.stop()


def test_openalex_queries_and_join_equivalence(spark) -> None:
    annual = spark.createDataFrame(
        [
            ("Q1", "One", 1900, 2, 1, 2, 100, 200.0, "ok"),
            ("Q1", "One", 1901, 0, 1, 0, 200, 0.0, "ok"),
            ("Q2", "Two", 1900, 3, 1, 3, 100, 300.0, "ok"),
        ],
        "artist_id string, artist_name string, year int, works_count long, "
        "is_complete long, analysis_works_count long, denominator_works long, "
        "works_per_10000 double, normalization_status string",
    )
    artists = spark.createDataFrame(
        [("Q1", "M05; M03"), ("Q2", "M03")],
        "artist_id string, movement_ids string",
    )
    assert q1_artist_series(annual, "Q1", 1800, 2019).count() == 2
    assert q2_year_snapshot(annual, 1900).count() == 2
    type_annual = spark.createDataFrame(
        [("Q1", 1900, "article", 2), ("Q2", 1900, "article", 3), ("Q1", 1901, "book", 1)],
        "artist_id string, year int, type string, works_count long",
    )
    type_rows = q4_type_distribution(type_annual, 1900, 1900).collect()
    assert len(type_rows) == 1
    assert type_rows[0]["artist_work_match_count"] == 5
    standard = q3_movement_year_membership_sum(
        annual, artists, broadcast_join=False
    )
    broadcast = q3_movement_year_membership_sum(
        annual, artists, broadcast_join=True
    )
    assert checksum_frame(standard) == checksum_frame(broadcast)
    m03_1900 = standard.filter("movement_id = 'M03' AND year = 1900").first()
    assert m03_1900["artist_membership_works_sum"] == 5

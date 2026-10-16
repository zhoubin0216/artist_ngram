from __future__ import annotations

import os
from pathlib import Path

import pytest


pyspark = pytest.importorskip("pyspark")
from pyspark.sql import SparkSession

from queries import checksum_frame, q1_artist_year, q2_year_range, q3_movement_year, q4_sales_vs_catalog
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
        .appName("artist-benchmark-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.autoBroadcastJoinThreshold", "-1")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )
    yield session
    session.stop()


def test_four_queries_and_join_equivalence(spark: SparkSession) -> None:
    events = spark.createDataFrame(
        [
            ("Q1", "O1", "E1", 1900, "confirmed_sale", "confirmed_title_transfer", 10.0, "GBP"),
            ("Q1", "O2", "E2", 1900, "catalog_entry", "catalog_presence_only", None, None),
            ("Q2", "O3", "E3", 1901, "catalog_entry", "catalog_presence_only", None, None),
        ],
        "artist_id string, object_id string, event_id string, event_year int, event_type string, transaction_status string, price_value double, currency string",
    )
    artists = spark.createDataFrame(
        [("Q1", "One", "Impressionism; Realism"), ("Q2", "Two", "Realism")],
        "artist_id string, canonical_name string, movement_names string",
    )
    assert q1_artist_year(events, "Q1").count() == 2
    assert q2_year_range(events, 1900, 1901).count() == 3
    assert q4_sales_vs_catalog(events).count() == 2
    standard = q3_movement_year(events, artists, broadcast_join=False)
    broadcast = q3_movement_year(events, artists, broadcast_join=True)
    assert checksum_frame(standard) == checksum_frame(broadcast)

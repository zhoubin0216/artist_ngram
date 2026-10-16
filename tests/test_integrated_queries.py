from __future__ import annotations

import os
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")
from pyspark.sql import SparkSession

from integrated_queries import movement_join_aggregate, query_outputs, select_query
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
        .appName("integrated-query-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.autoBroadcastJoinThreshold", "-1")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )
    yield session
    session.stop()


def test_three_queries_sources_summary_and_join(spark: SparkSession) -> None:
    rows = [
        ("ngram", "artist", "Q1", "One", 1900, "artist_observed", "match_count", 2.0, "One", 1, "observed"),
        ("openalex", "artist", "Q1", "One", 1900, "artist_complete_grid", "works_count", 3.0, None, None, "ok"),
        ("getty", "artist", "Q1", "One", 1900, "artist_observed", "confirmed_sale_count", 1.0, None, None, "observed"),
        ("ngram", "movement", "M05", "Impressionism", 1900, "direct_movement_term", "match_count", 4.0, "Impressionism", 1, "observed"),
        ("openalex", "movement", "M05", "impressionism", 1900, "direct_official_union", "union_works_count", 5.0, None, None, "ok"),
    ]
    metrics = spark.createDataFrame(
        rows,
        "source string, entity_type string, entity_id string, entity_name string, year long, "
        "series_kind string, metric string, value double, query_term string, n long, availability string",
    )
    kwargs = dict(
        artist_id="Q1",
        movement_id="M05",
        artist_year_start=1800,
        artist_year_end=2019,
        movement_year_start=1850,
        movement_year_end=1920,
        period_year_start=1900,
        period_year_end=1900,
    )
    artist = query_outputs(select_query(metrics, "artist", **kwargs))
    assert artist["ngram"].count() == 1
    assert artist["openalex"].count() == 1
    assert artist["getty"].count() == 1
    assert artist["summary"].count() == 1
    movement = query_outputs(select_query(metrics, "movement", **kwargs))
    assert movement["ngram"].count() == 1
    assert movement["openalex"].count() == 1
    assert movement["summary"].count() == 1
    period = query_outputs(select_query(metrics, "period", **kwargs))
    assert period["summary"].count() == 2

    bridge = spark.createDataFrame(
        [("Q1", "M05", "Impressionism")],
        "artist_id string, movement_id string, movement_name string",
    )
    standard = movement_join_aggregate(metrics, bridge, broadcast_join=False)
    broadcast = movement_join_aggregate(metrics, bridge, broadcast_join=True)
    assert checksum_frame(standard) == checksum_frame(broadcast)

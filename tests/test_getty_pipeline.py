from __future__ import annotations

import pandas as pd

from matching import choose_candidate, score_candidate
from run_pipeline import build_artist_year, build_events, classify_catalog_source


def test_exact_name_and_years_score_high() -> None:
    artist = {
        "canonical_name": "Claude Monet",
        "birth_year": "1840",
        "death_year": "1926",
    }
    candidate = {
        "candidate_name": "Claude Monet",
        "getty_preferred_name": "MONET, CLAUDE",
        "birth_begin": "1840-01-01T00:00:00Z",
        "death_begin": "1926-01-01T00:00:00Z",
    }
    scored = score_candidate(artist, candidate, ["Monet, Claude", "Claude Monet"])
    assert scored.score == 1.0
    assert "birth_year_exact" in scored.evidence


def test_ambiguous_candidates_require_manual_review() -> None:
    artist = {
        "canonical_name": "Example Artist",
        "birth_year": "1900",
        "death_year": "1980",
    }
    candidates = [
        {
            "person": f"https://example.test/{index}",
            "candidate_name": "Example Artist",
            "getty_preferred_name": "EXAMPLE, ARTIST",
            "birth_begin": "1900-01-01",
            "death_begin": "1980-01-01",
        }
        for index in (1, 2)
    ]
    result = choose_candidate(
        artist,
        candidates,
        ["Example Artist"],
        low_confidence_threshold=0.8,
        ambiguity_margin=0.15,
    )
    assert result["manual_review"] is True
    assert result["getty_person_uri"] == ""
    assert result["candidate_count"] == 2


def test_catalog_rows_are_not_counted_as_sales() -> None:
    mapping = pd.DataFrame(
        [{"artist_id": "Q1", "getty_person_uri": "https://example.test/person"}]
    )
    sales = [
        {
            "person": "https://example.test/person",
            "object": "https://example.test/object",
            "event": "https://example.test/sale",
            "event_begin": "1901-01-01",
            "price_value": "10",
            "currency_label": "pounds",
        }
    ]
    catalogs = [
        {
            "person": "https://example.test/person",
            "object": "https://example.test/object",
            "record": "https://example.test/catalog",
            "record_begin": "1901-01-01",
            "record_label": "Sales Catalog Entry 12",
        }
    ]
    events = build_events(mapping, sales, catalogs, "2026-10-13T00:00:00+00:00")
    aggregate = build_artist_year(events)
    assert len(events) == 2
    assert aggregate.iloc[0]["confirmed_sale_count"] == 1
    assert aggregate.iloc[0]["catalog_entry_count"] == 1
    assert set(events["transaction_status"]) == {
        "confirmed_title_transfer",
        "catalog_presence_only",
    }


def test_source_classification() -> None:
    assert (
        classify_catalog_source("Knoedler Stock Book 8", "", "")
        == "dealer_stock_book_entry"
    )
    assert (
        classify_catalog_source("Paris Sales Catalog lot 4", "", "")
        == "sales_catalog_entry"
    )

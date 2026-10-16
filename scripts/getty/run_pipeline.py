from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from common import (
    GETTY_DIR,
    binding_rows,
    chunks,
    ensure_directories,
    fetch_provenance_record,
    fetch_ulan,
    load_config,
    normalise_name,
    query_sparql,
    read_template,
    sparql_escape,
    ulan_names,
    utc_now,
    write_parquet_and_csv,
    year_from_value,
)
from matching import choose_candidate
from select_sample import select_sample


MAPPING_COLUMNS = [
    "artist_id",
    "wikidata_qid",
    "canonical_name",
    "birth_year",
    "death_year",
    "ulan_id",
    "getty_person_uri",
    "getty_preferred_name",
    "match_method",
    "match_confidence",
    "manual_review",
    "source_url",
    "candidate_count",
    "matched_birth_year",
    "matched_death_year",
    "match_evidence",
]

EVENT_COLUMNS = [
    "artist_id",
    "getty_person_uri",
    "object_id",
    "event_id",
    "event_year",
    "event_date",
    "source_type",
    "event_type",
    "transaction_status",
    "price_value",
    "currency",
    "attribution_label",
    "catalog_or_record_id",
    "source_url",
    "retrieved_at",
]


def first_nonempty(values: pd.Series) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def uri_tail(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1] if value else ""


def get_ulan_evidence(
    sample: pd.DataFrame, config: dict[str, Any]
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}

    def one(row: dict[str, str]) -> tuple[str, list[str]]:
        ulan_id = str(row["ulan_id"])
        payload = fetch_ulan(ulan_id, config)
        return str(row["artist_id"]), ulan_names(payload, ulan_id)

    with ThreadPoolExecutor(max_workers=min(5, len(sample))) as executor:
        futures = [executor.submit(one, row) for row in sample.to_dict("records")]
        for future in as_completed(futures):
            artist_id, names = future.result()
            result[artist_id] = names
    return result


def lookup_names(artist: dict[str, Any], ulan_aliases: list[str]) -> list[str]:
    canonical = str(artist["canonical_name"]).strip()
    candidates = [canonical, *ulan_aliases]
    if " " in canonical:
        given, surname = canonical.rsplit(" ", 1)
        candidates.append(f"{surname}, {given}")
    unique: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        key = normalise_name(value)
        if len(key) < 4 or key in seen:
            continue
        seen.add(key)
        unique.append(value)
        if len(unique) >= 12:
            break
    return unique


def build_mapping(
    sample: pd.DataFrame,
    config: dict[str, Any],
    run_id: str,
) -> pd.DataFrame:
    ulan_evidence = get_ulan_evidence(sample, config)
    template = read_template("artist_candidates.sparql")
    candidate_rows: list[dict[str, str]] = []
    records = sample.to_dict("records")
    for chunk_index, artist_chunk in enumerate(
        chunks(records, int(config["mapping_chunk_size"])), start=1
    ):
        if len(artist_chunk) != 1:
            raise ValueError("mapping_chunk_size must remain 1 for indexed literal queries")
        artist = artist_chunk[0]
        aliases = ulan_evidence.get(str(artist["artist_id"]), [])
        for alias_index, name in enumerate(lookup_names(artist, aliases)[:4], start=1):
            escaped = sparql_escape(name)
            query = template.replace(
                "{{artist_id}}", sparql_escape(str(artist["artist_id"]))
            ).replace("{{lookup_name}}", escaped)
            payload = query_sparql(
                query,
                raw_name=(
                    f"{run_id}_artist_candidates_{chunk_index:02d}_"
                    f"{alias_index:02d}.json"
                ),
                config=config,
            )
            rows = binding_rows(payload)
            for row in rows:
                person_payload = fetch_provenance_record(row["person"], config)
                row["getty_preferred_name"] = person_payload.get(
                    "_label", row.get("getty_preferred_name", "")
                )
                row["birth_begin"] = (
                    person_payload.get("born", {}).get("timespan", {}).get(
                        "begin_of_the_begin", ""
                    )
                )
                row["death_begin"] = (
                    person_payload.get("died", {}).get("timespan", {}).get(
                        "begin_of_the_begin", ""
                    )
                )
            candidate_rows.extend(rows)
            if rows:
                break

    by_artist: dict[str, list[dict[str, str]]] = {}
    for row in candidate_rows:
        by_artist.setdefault(row.get("artist_id", ""), []).append(row)

    mapping_rows: list[dict[str, Any]] = []
    for artist in records:
        artist_id = str(artist["artist_id"])
        chosen = choose_candidate(
            artist,
            by_artist.get(artist_id, []),
            ulan_evidence.get(artist_id, []),
            low_confidence_threshold=float(config["low_confidence_threshold"]),
            ambiguity_margin=float(config["ambiguity_margin"]),
        )
        base = {
            key: artist.get(key, "")
            for key in (
                "artist_id",
                "wikidata_qid",
                "canonical_name",
                "birth_year",
                "death_year",
                "ulan_id",
            )
        }
        mapping_rows.append({**base, **chosen})
    return pd.DataFrame(mapping_rows, columns=MAPPING_COLUMNS)


def run_event_queries(
    mapping: pd.DataFrame,
    config: dict[str, Any],
    run_id: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    mapped = mapping[mapping["getty_person_uri"].astype(str).str.len() > 0]
    person_uris = mapped["getty_person_uri"].drop_duplicates().tolist()
    sales_template = read_template("confirmed_sales.sparql")
    catalog_template = read_template("catalog_entries.sparql")
    sales_rows: list[dict[str, str]] = []
    catalog_rows: list[dict[str, str]] = []
    for chunk_index, person_chunk in enumerate(
        chunks(person_uris, int(config["event_chunk_size"])), start=1
    ):
        values = "\n    ".join(f"<{uri}>" for uri in person_chunk)
        replacements = {
            "{{person_values}}": values,
            "{{event_limit}}": str(config["event_limit_per_chunk"]),
        }
        sales_query = sales_template
        catalog_query = catalog_template
        for needle, replacement in replacements.items():
            sales_query = sales_query.replace(needle, replacement)
            catalog_query = catalog_query.replace(needle, replacement)
        sales_rows.extend(
            binding_rows(
                query_sparql(
                    sales_query,
                    raw_name=f"{run_id}_confirmed_sales_{chunk_index:02d}.json",
                    config=config,
                )
            )
        )
        catalog_rows.extend(
            binding_rows(
                query_sparql(
                    catalog_query,
                    raw_name=f"{run_id}_catalog_entries_{chunk_index:02d}.json",
                    config=config,
                )
            )
        )
    return sales_rows, catalog_rows


def classify_catalog_source(label: str, type_label: str, type_uri: str) -> str:
    combined = f"{label} {type_label} {type_uri}".casefold()
    if "stock book" in combined or "account book" in combined or "300027483" in combined:
        return "dealer_stock_book_entry"
    if "catalog" in combined or "auction" in combined:
        return "sales_catalog_entry"
    return "provenance_source_entry"


def build_events(
    mapping: pd.DataFrame,
    sales_rows: list[dict[str, str]],
    catalog_rows: list[dict[str, str]],
    retrieved_at: str,
) -> pd.DataFrame:
    person_to_artist = dict(
        zip(mapping["getty_person_uri"].astype(str), mapping["artist_id"].astype(str))
    )
    output: list[dict[str, Any]] = []

    if sales_rows:
        sales = pd.DataFrame(sales_rows).fillna("")
        for (_, object_uri, event_uri), group in sales.groupby(
            ["person", "object", "event"], dropna=False, sort=False
        ):
            first = {column: first_nonempty(group[column]) for column in group.columns}
            event_date = first.get("event_date") or first.get("event_begin", "")[:10]
            price_raw = first.get("price_value", "")
            try:
                price_value: float | None = float(price_raw) if price_raw else None
            except ValueError:
                price_value = None
            output.append(
                {
                    "artist_id": person_to_artist.get(str(first.get("person", "")), ""),
                    "getty_person_uri": first.get("person", ""),
                    "object_id": object_uri,
                    "event_id": event_uri,
                    "event_year": year_from_value(first.get("event_begin") or event_date),
                    "event_date": event_date,
                    "source_type": "provenance_title_transfer",
                    "event_type": "confirmed_sale",
                    "transaction_status": "confirmed_title_transfer",
                    "price_value": price_value,
                    "currency": first.get("currency_label") or first.get("currency", ""),
                    "attribution_label": first.get("attribution_label", ""),
                    "catalog_or_record_id": uri_tail(first.get("source_record", "")),
                    "source_url": event_uri,
                    "retrieved_at": retrieved_at,
                }
            )

    if catalog_rows:
        catalog = pd.DataFrame(catalog_rows).fillna("")
        for (_, object_uri, record_uri), group in catalog.groupby(
            ["person", "object", "record"], dropna=False, sort=False
        ):
            first = {column: first_nonempty(group[column]) for column in group.columns}
            record_date = first.get("record_date") or first.get("record_begin", "")[:10]
            output.append(
                {
                    "artist_id": person_to_artist.get(str(first.get("person", "")), ""),
                    "getty_person_uri": first.get("person", ""),
                    "object_id": object_uri,
                    "event_id": record_uri,
                    "event_year": year_from_value(first.get("record_begin") or record_date),
                    "event_date": record_date,
                    "source_type": classify_catalog_source(
                        first.get("record_label", ""),
                        first.get("record_type_label", ""),
                        first.get("record_type", ""),
                    ),
                    "event_type": "catalog_entry",
                    "transaction_status": "catalog_presence_only",
                    "price_value": None,
                    "currency": "",
                    "attribution_label": first.get("attribution_label", ""),
                    "catalog_or_record_id": first.get("record_identifier")
                    or uri_tail(record_uri),
                    "source_url": record_uri,
                    "retrieved_at": retrieved_at,
                }
            )

    events = pd.DataFrame(output, columns=EVENT_COLUMNS)
    if events.empty:
        events = pd.DataFrame(columns=EVENT_COLUMNS)
    events["event_year"] = pd.to_numeric(events["event_year"], errors="coerce").astype("Int64")
    events["price_value"] = pd.to_numeric(events["price_value"], errors="coerce")
    events = events.drop_duplicates(
        subset=["artist_id", "object_id", "event_id", "event_type"]
    ).sort_values(["artist_id", "event_year", "event_type", "event_id"], na_position="last")
    return events.reset_index(drop=True)


def build_artist_year(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(
            columns=[
                "artist_id",
                "event_year",
                "confirmed_sale_count",
                "catalog_entry_count",
                "total_record_count",
            ]
        )
    counted = events.dropna(subset=["event_year"]).copy()
    counted["confirmed_sale_count"] = (counted["event_type"] == "confirmed_sale").astype(int)
    counted["catalog_entry_count"] = (counted["event_type"] == "catalog_entry").astype(int)
    result = (
        counted.groupby(["artist_id", "event_year"], as_index=False)
        .agg(
            confirmed_sale_count=("confirmed_sale_count", "sum"),
            catalog_entry_count=("catalog_entry_count", "sum"),
            total_record_count=("event_id", "count"),
        )
        .sort_values(["artist_id", "event_year"])
    )
    result["event_year"] = result["event_year"].astype("Int64")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Getty Provenance pilot pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Number of pilot artists")
    parser.add_argument(
        "--mapping-only", action="store_true", help="Stop after artist mapping"
    )
    args = parser.parse_args()

    ensure_directories()
    config = load_config()
    limit = args.limit or int(config["pilot_size"])
    run_id = re.sub(r"[^0-9]", "", utc_now())[:14]
    retrieved_at = utc_now()

    sample = select_sample(limit)
    sample.to_csv(GETTY_DIR / "sample_artists.csv", index=False, encoding="utf-8")
    mapping = build_mapping(sample, config, run_id)
    mapping.to_csv(GETTY_DIR / "getty_artist_mapping.csv", index=False, encoding="utf-8")

    if args.mapping_only:
        print(mapping["match_method"].value_counts(dropna=False).to_string())
        return

    sales_rows, catalog_rows = run_event_queries(mapping, config, run_id)
    events = build_events(mapping, sales_rows, catalog_rows, retrieved_at)
    artist_year = build_artist_year(events)
    write_parquet_and_csv(events, GETTY_DIR / "getty_events")
    write_parquet_and_csv(artist_year, GETTY_DIR / "getty_artist_year")

    summary = {
        "pilot_artists": len(sample),
        "matched_artists": int((mapping["getty_person_uri"].astype(str).str.len() > 0).sum()),
        "manual_review": int(mapping["manual_review"].astype(bool).sum()),
        "unmatched": int((mapping["match_method"] == "unmatched").sum()),
        "event_rows": len(events),
        "confirmed_sales": int((events["event_type"] == "confirmed_sale").sum()),
        "catalog_entries": int((events["event_type"] == "catalog_entry").sum()),
        "retrieved_at": retrieved_at,
    }
    (GETTY_DIR / "pilot_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

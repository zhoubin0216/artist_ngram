# Getty Provenance data dictionary

The pipeline uses the Getty Provenance Index SPARQL endpoint and Linked Art/CIDOC CRM paths verified against live responses. Raw SPARQL and ULAN JSON-LD responses are saved under `data/getty/raw/` and ignored by Git.

## Artist mapping

`data/getty/full/getty_artist_mapping.csv` contains one row per project artist.
The full run attempts all 1,370 artists. The original pilot files at
`data/getty/` remain separate for audit.
`data/getty/full/getty_mapping_review.csv` is the 875-row review subset and
retains candidate URIs without accepting them as matches.

| Field | Meaning |
|---|---|
| `artist_id` | Stable project artist identifier. Currently the Wikidata QID. |
| `wikidata_qid` | Wikidata entity identifier. |
| `canonical_name` | Canonical English name from the artist dictionary. |
| `birth_year`, `death_year` | Dictionary years. Missing values stay empty. |
| `ulan_id` | Getty ULAN identifier from Wikidata. It is supporting evidence, not a direct Provenance URI join key. |
| `getty_person_uri` | Accepted Getty Provenance `E21_Person` URI. Empty when not accepted. |
| `getty_preferred_name` | Getty Provenance record label. |
| `match_method` | Evidence used by the deterministic scorer. |
| `match_confidence` | Score from 0 to 1. It is a matching aid, not a statistical probability. |
| `manual_review` | `true` for low-confidence, ambiguous, or unmatched cases. |
| `source_url` | Accepted Getty person URL. |
| `candidate_count` | Distinct candidate Getty person records returned. |
| `candidate_uris` | Semicolon-delimited candidate URIs retained for manual review. |
| `matched_birth_year`, `matched_death_year` | Years extracted from the Getty candidate. |
| `match_evidence` | Semicolon-delimited evidence flags. |

The pipeline downloads each ULAN record and uses its preferred and alternate names to broaden exact-name candidate discovery. It never constructs or accepts a Getty Provenance person URI from a ULAN ID. A candidate is accepted only when it is unique and reaches the configured 0.80 confidence threshold. Manual-review rows have an empty `getty_person_uri` and are excluded from event retrieval.

## Event detail

`data/getty/full/getty_events.csv` and
`data/getty/full/getty_events.parquet` share the same rows and schema.

| Field | Meaning |
|---|---|
| `artist_id` | Project artist identifier. |
| `getty_person_uri` | Matched Getty person URI. |
| `object_id` | Getty `HumanMadeObject` URI. |
| `event_id` | Getty activity URI or source-entry URI. |
| `event_year` | Year parsed from Getty's event/source timespan. Empty when Getty has no usable year. |
| `event_date` | Getty date label or lower-bound date. No missing dates are inferred. |
| `source_type` | `provenance_title_transfer`, `dealer_stock_book_entry`, `sales_catalog_entry`, or `provenance_source_entry`. |
| `event_type` | `confirmed_sale` or `catalog_entry`. |
| `transaction_status` | `confirmed_title_transfer` only when an `E8_Acquisition` transfers title of the object; catalog rows are `catalog_presence_only`. |
| `price_value` | Numeric amount attached to the activity, without currency conversion. |
| `currency` | Getty currency label or URI. Different currencies remain separate. |
| `attribution_label` | Getty label on the production sub-event that links the person to the object. |
| `catalog_or_record_id` | STAR/source identifier when present, otherwise the source URI UUID. |
| `source_url` | Getty activity or source-entry URL. |
| `retrieved_at` | UTC retrieval timestamp. |

## Artist-year aggregate

`data/getty/full/getty_artist_year.csv` and `.parquet` aggregate by `artist_id` and `event_year`.

- `confirmed_sale_count` counts only title-transfer activities.
- `catalog_entry_count` counts source entries and never implies a sale.
- `total_record_count` is the sum of the two row classes.

## Reproducible commands

```powershell
.\.venv\Scripts\python.exe scripts\getty\run_pipeline.py --limit 5
.\.venv\Scripts\python.exe scripts\getty\run_pipeline.py --limit 25
.\.venv\Scripts\python.exe scripts\getty\run_pipeline.py --scope all --mapping-only
.\.venv\Scripts\python.exe scripts\getty\run_pipeline.py --scope all --events-only
```

The SPARQL templates are stored in `queries/getty/`. Event queries use stable
ordering plus `LIMIT`/`OFFSET` pagination. Raw ULAN, person and SPARQL responses
are cached, so interrupted full runs can resume without repeating completed
requests.

## Full-run coverage (retrieved 2026-10-17)

- Dictionary artists attempted: 1,370
- Automatically accepted mappings: 495
- Candidate-bearing manual-review rows: 122
- Rows without a candidate: 753
- Artists with at least one event: 450
- Deduplicated events: 79,715
- Confirmed sales: 39,388
- Catalog entries: 40,327
- Events with a usable year: 23,007
- Events without a usable year: 56,708

The pipeline covers the full dictionary in the sense that every artist is
attempted and represented in the mapping table. It does not claim that Getty
contains a usable person record or dated event for every artist.

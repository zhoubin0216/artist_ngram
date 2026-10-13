# European Artist Visibility Project

This is a standalone data-preparation project for studying the historical visibility of European artists in the English Google Books N-gram corpus.

## Project contents

- `scripts/build_artist_dictionary.mjs` builds the Excel artist dictionary.
- `scripts/build_project_proposal.py` builds the short Word proposal.
- `scripts/ngram_sample.py` plans targeted downloads, resumes and verifies raw shards, and validates exact-name yearly frequencies.
- `tests/test_ngram_sample.py` tests the download and validation workflow offline.
- `docs/ngram_workflow.md` contains the Chinese step-by-step workflow and interpretation notes.
- [Ngram data guide for teammates（数据使用说明）](docs/ngram_data_guide.md) explains the completed artist/movement datasets, fields, joins, Python examples, and sharing requirements.
- `data/wikidata/` contains the downloaded Wikidata source records.
- `docs/` contains the construction plan and submitted proposal.
- `outputs/` contains the generated artist dictionary.
- `outputs/ngram_artists_full/` and `outputs/ngram_movement_15/` contain the committed compact Ngram datasets (73,523 artist-year and 2,378 movement-year records).

The current baseline contains 15 movements and 1,370 European painters. Each artist has a Wikidata QID, canonical English name, birth and death years, nationality, movement mapping, and Getty ULAN ID.

## Open in PyCharm

Open this directory as a separate project. Create a new Python interpreter for it instead of reusing the `2221lab` project environment.

## Ngram download and small-sample validation

Use Python 3.9+ from the project root. This workflow uses only the standard library; no Spark, HDFS, Excel package, or additional installation is required for the smoke test.

```bash
# One-artist smoke test: about 501 MiB, English corpus 20200217, years 1800–2019.
python scripts/ngram_sample.py plan --artists Q296 --manifest data/ngram/smoke_manifest.json
python scripts/ngram_sample.py download --manifest data/ngram/smoke_manifest.json --max-gib 1
python scripts/ngram_sample.py validate --manifest data/ngram/smoke_manifest.json --output outputs/ngram_smoke --require-all

# A 25-artist download plan only: inspect cost before downloading.
python scripts/ngram_sample.py plan --sample-size 25

# Synthetic offline tests; localhost socket access is required.
python -m unittest discover -s tests -v
```

Open `outputs/ngram_smoke/report.html` to view the result. Read the [complete workflow](docs/ngram_workflow.md) before scaling up. The two reviewed full-result directories are tracked, including counts, metadata, totals and checkpoints. Raw shards, sample outputs, temporary downloads, locks and runtime progress are Git-ignored. The redundant 50-artist output was removed after verifying it is included in the full dataset.

For normal analysis, use the committed datasets; do not re-download the corpus. The [data guide](docs/ngram_data_guide.md) contains dependency-free Python examples. The extraction/recovery scripts require macOS/Linux (`fcntl`); reading the JSON/JSONL data works on Windows too. The full artist downloader no longer requires a pilot directory, and uses the published manifest by default.

## Python setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_project_proposal.py
```

## Dictionary build

The spreadsheet builder uses `@oai/artifact-tool`. Run it in an environment where that package is available:

```bash
node scripts/build_artist_dictionary.mjs
```

Generated files are written to `outputs/`. Preview files are written to `.tmp/`.

## OpenAlex methodology

**Data source:** OpenAlex Works REST API (`https://api.openalex.org/works`).

- Publication dates: **1800-01-01 to 2019-12-31** (inclusive).
- Language: OpenAlex's `language=en` label. Some metadata may be mislabeled.
- Corpus: **`core`**.
- Subject: **Visual Arts and Performing Arts**, OpenAlex subfield **1213**. Include a work if **any** assigned topic belongs to the subfield (`topics.subfield.id:1213`); `primary_topic` is recorded separately.
- Work types: **all types**; `type` is preserved and summarized.
- Name matching: only the canonical **full English name** from the existing artist dictionary, in the title or reconstructed abstract. No aliases, surname-only searches, full-text search, or OpenAlex author-identity matching.
- Counting unit: **one distinct OpenAlex Work ID per artist and publication year**. A work mentioning two different artists may be counted once for each artist.
- Filtering and QA: API candidates are retained; local title/abstract matching and standard filters determine the collected mention candidates. The cleaning step then applies documented human decisions. Topic/abstract risk flags alone are **not** automatic exclusions.

This is a reproducible **art-classified, full-name mention proxy**, not a claim that every work has been individually entity-disambiguated. OpenAlex's historical coverage, abstract availability, language labels, and topic classification vary over time. Raw OpenAlex counts should not be treated as directly comparable to normalized Ngram frequencies, nor as a complete census of historical scholarship.

## OpenAlex setup and execution (Windows PowerShell)

Open PowerShell at the **repository root** (the directory containing `scripts/` and `config/`). Activate the existing virtual environment if needed:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-openalex.txt
```

**Tests (no API access needed):**

```powershell
python -m unittest discover -s tests -p "test_openalex_collector.py" -v
python -m unittest discover -s tests -p "test_openalex_art_audit.py" -v
python -m unittest discover -s tests -p "test_clean_openalex.py" -v
```

### Data collection (already completed for the study)

To reproduce collection **only when required**, first set your own API key (never commit it):

```powershell
$env:OPENALEX_API_KEY="YOUR_OWN_API_KEY"
python scripts/collect_openalex.py --mode full --topic-scope any --corpus core
```

The collector writes to `data/openalex_art/core/any/full/`, including:

```text
data/openalex_art/core/any/full/
  raw/                           # locally verified artist–work matches; KEEP
  candidates/                    # API candidates, including locally rejected ones; KEEP for audit
  collection_status.json         # per-artist checkpoint
  artist_collection_status.csv   # one status row per artist
  artist_year.csv                # pre-cleaning sparse annual counts
  artist_year_type.csv           # pre-cleaning type breakdown
  audit_sample.csv               # saved manual QA sample
```

If all 1,370 artists are already marked `complete`, **do not re-collect data just to regenerate cleaned outputs**. To regenerate *pre-cleaning* summaries from the stored raw collection, use `--summary-only`:

```powershell
python scripts/collect_openalex.py --mode full --topic-scope any --corpus core --summary-only
```

The --artists and --limit-artists options control collection only. Summaries always use the full dictionary.

### Final cleaning (run locally to generate deliverables)

The final policy was agreed after reviewing 150 full-run sample records and four additional known issues. **10 confirmed wrong-entity matches** and **4 precautionary exclusions** (uncertain identity or unreliable date/language metadata) are excluded from the *final analysis*, but never deleted from the source JSONL. Other records are retained under the declared matching rules, without claiming full individual verification.

Required files:

```text
scripts/clean_openalex.py
config/openalex_manual_review_150.csv
config/openalex_known_issues.csv
data/openalex_art/core/any/full/raw/
data/openalex_art/core/any/full/artist_collection_status.csv
data/openalex_art/core/any/full/artist_year.csv
data/openalex_art/core/any/full/artist_year_type.csv
```

Run the cleaning step **yourself on your own machine**:

```powershell
python scripts/clean_openalex.py
```

Equivalent command with explicit locations:

```powershell
python scripts/clean_openalex.py `
  --input-dir "data/openalex_art/core/any/full" `
  --reviews "config/openalex_manual_review_150.csv" `
  --known-issues "config/openalex_known_issues.csv" `
  --output-dir "results/openalex"
```

The script uses the Python standard library and **does not require an API key or network connection**. It reads the raw records and reviews, verifies collection completeness and aggregate consistency, writes new outputs, and does **not** overwrite the original source data.

Expected for the currently reviewed input snapshot:

| Check | Expected |
| --- | ---: |
| Complete artists | 1,370 |
| Pre-cleaning artist–work matches | 15,693 |
| Cleaned artist–work matches | 15,679 |
| Exclusions | 14 (10 confirmed wrong entity + 4 precautionary) |
| Full annual grid | 301,400 rows (1,370 artists × 220 years) |

If your results differ, inspect `results/openalex/cleaning_summary.json` and confirm that the input collection and review CSVs match the agreed snapshot. Do not silently edit counts to force agreement.

### OpenAlex annual normalization

The agreed scope is **art-topic works, all document types**. Keep the existing cleaned artist counts; normalization does not require collecting artist mentions again.

The denominator is the number of distinct OpenAlex works in each year meeting the same filters: **core corpus, English metadata, publication dates 1800-01-01 through 2019-12-31, and any topic in subfield 1213**. It has **no artist-name search condition** and is not the sum of artist hits.

Run at the repository root. The first command accesses the API; the second works offline:

```powershell
$env:OPENALEX_API_KEY="YOUR_OWN_API_KEY"
python scripts/normalize_openalex.py fetch --types all
python scripts/normalize_openalex.py normalize
```

For an already saved denominator snapshot, run only `normalize`. Fetching again replaces the saved denominator snapshot and may change values as OpenAlex updates. These commands preserve the original collected data and cleaned outputs; they write to separate directories:

```text
data/openalex_denominator/
  annual_denominator.csv
  annual_denominator_response.json
  denominator_manifest.json
results/openalex_normalized/
  artist_year_normalized.csv
  normalization_summary.json
```

Formula: **works_per_10000 = analysis_works_count / denominator_works × 10,000**. With `--types all`, `analysis_works_count` equals the original cleaned `works_count`.

The locally verified snapshot fetched on **2026-10-13** contains 220 annual denominators totaling **1,961,720 works**; every year has a positive denominator. The normalized table has **301,400 rows, 1,370 artists and 15,679 retained artist-work matches**. These are snapshot checks, not values to hard-code into future runs.

The script validates grouped API totals, pagination, the saved API response hash, year coverage and numerator consistency, and records cleaned-input hashes. The current implementation does not compare each annual denominator CSV value against the raw API response; preserve both files and avoid manual edits. Raw responses and query scope/time are saved for audit. A zero denominator produces an empty normalized value with `normalization_status=zero_denominator`; zero artist hits with a positive denominator produce a normalized zero. Never convert unavailable normalized values to zero.

Normalization adjusts annual indexed corpus size. It does not eliminate historical coverage gaps, missing abstracts, topic/language errors or identity ambiguity. Small early-year denominators can make rates unstable; inspect coverage before interpreting peaks. The denominator fetch and artist collection may be different snapshots; record their provenance separately.

## OpenAlex files for the integration contributor

The cleaning command creates these files in `results/openalex/`:

| File | Role |
| --- | --- |
| **`artist_year_dense_clean.csv`** | **Raw-count join input**: every collected artist × year (1800–2019), including explicit zero counts. Join using `artist_id` + `year`; use `works_count`. |
| `artist_year_clean.csv` | Sparse version: only artist-year pairs with at least one retained work; includes optional QA count fields. |
| `artist_year_type_clean.csv` | Type-specific counts. |
| `artist_collection_status.csv` | Collection-completeness evidence for all artists. |
| `artist_cleaning_status.csv` | Per-artist before/after cleaning summary. |
| `cleaning_summary.json` | Input/decision provenance, expected counts and cleaning results. |
| `quality_report.md` | Method, remaining limitations, and cleaning policy. |
| `excluded_from_analysis.csv` | Exclusion audit trail (14 rows for this version). |
| `possible_duplicate_titles.csv` | Additional *warnings only*; shared titles are not automatically treated as duplicate works. |
| `manual_decisions_applied.csv` | All applied human decisions, including retained and excluded records. |
| `held_and_excluded.csv` | Compatibility copy of `excluded_from_analysis.csv`; no separate hold category is used. |

The cleaned work-level JSONL is useful for **internal audit**, but **is not required** for the annual join. Do not send the raw API cache unless a teammate explicitly needs it.

**Meaning of zero:** `works_count=0` in the dense file indicates **no matched works under the specified OpenAlex criteria for an artist whose collection completed**. It is not the same thing as missing collection data. Keep `artist_collection_status.csv` to verify completeness.

**Unit caution:** OpenAlex `works_count` is a count of distinct works mentioning an artist; Ngram uses normalized word-string frequency; Getty uses provenance/transaction records. Do not sum them together. Use the additional normalized OpenAlex table below for corpus-size-adjusted trends, retaining raw counts for interpretation. The three sources still have different units after OpenAlex normalization.

### Cleaned-count handoff ZIP (PowerShell)

After the cleaning command has succeeded, create a small ZIP in the repository root:

```powershell
$src = "results/openalex"
$names = @(
  "artist_year_dense_clean.csv",
  "artist_year_clean.csv",
  "artist_year_type_clean.csv",
  "artist_collection_status.csv",
  "artist_cleaning_status.csv",
  "cleaning_summary.json",
  "quality_report.md",
  "excluded_from_analysis.csv"
)
$paths = $names | ForEach-Object { Join-Path $src $_ }
$missing = $paths | Where-Object { -not (Test-Path $_) }
if ($missing) { throw "Missing files: $($missing -join ', ')" }
Compress-Archive -Path $paths -DestinationPath "OpenAlex_clean_handoff.zip" -Force
```

The ZIP is generated **locally**. Share it with the team member responsible for the annual cross-source join; they do not need to call the OpenAlex API.

### Normalized OpenAlex handoff

For annual trend comparisons, join **`results/openalex_normalized/artist_year_normalized.csv`** on `artist_id` + `year`. Use `works_per_10000` as the normalized OpenAlex metric and retain `works_count` alongside it.

| Field | Meaning |
| --- | --- |
| `artist_id`, `year` | Wikidata QID and publication year; unique join key |
| `works_count` | Original cleaned distinct-work count |
| `analysis_works_count` | Count within selected types; identical to `works_count` for the agreed all-type scope |
| `denominator_works` | Same-scope annual OpenAlex corpus size |
| `works_per_10000` | Artist hits per 10,000 same-scope works |
| `is_complete` | Artist collection completion indicator |
| `normalization_status` | `ok` or `zero_denominator` |

The ZIP in the cleaned-count handoff section contains the cleaned counts only. To deliver counts **and normalization**, create an additional ZIP after normalization succeeds:

```powershell
$paths = @(
  "results/openalex/artist_year_dense_clean.csv",
  "results/openalex/artist_year_clean.csv",
  "results/openalex/artist_year_type_clean.csv",
  "results/openalex/artist_collection_status.csv",
  "results/openalex/artist_cleaning_status.csv",
  "results/openalex/cleaning_summary.json",
  "results/openalex/quality_report.md",
  "results/openalex/excluded_from_analysis.csv",
  "results/openalex/possible_duplicate_titles.csv",
  "results/openalex_normalized/artist_year_normalized.csv",
  "results/openalex_normalized/normalization_summary.json",
  "data/openalex_denominator/annual_denominator.csv",
  "data/openalex_denominator/denominator_manifest.json"
)
$missing = $paths | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missing) { throw "Missing files: $($missing -join ', ')" }
Compress-Archive -Path $paths -DestinationPath "OpenAlex_normalized_handoff.zip" -Force
```

The original API response remains a local audit artifact. If cleaned inputs change, rerun `normalize` using the chosen saved denominator snapshot, then rebuild this ZIP. Do not edit normalized CSV values manually.

## OpenAlex files and local storage

The current OpenAlex workflow uses these existing scripts and configuration files:

```text
scripts/
  collect_openalex.py
  audit_openalex_art.py
  clean_openalex.py
  normalize_openalex.py
config/
  openalex_manual_review_150.csv
  openalex_known_issues.csv
tests/
  test_openalex_collector.py
  test_openalex_art_audit.py
  test_clean_openalex.py
requirements-openalex.txt
```

Keep `data/openalex_art/core/any/full/` locally for collection checkpoints, candidate verification and reproducible cleaning. Keep `data/openalex_denominator/` for the chosen annual denominator snapshot. The cleaned and normalized outputs are stored in `results/openalex/` and `results/openalex_normalized/`, respectively.

The current `.gitignore` excludes `data/openalex_art/`, `results/openalex/artist_work_clean.jsonl` and `OpenAlex_clean_handoff.zip`. It does not currently exclude the denominator directory or the normalized handoff ZIP. Keep the small denominator CSV and manifest available to the integration contributor; whether they are committed is a team decision. Never commit an API key.

The ZIP paths in the handoff sections are generated deliverables, not required source files. Create the normalized handoff ZIP using the normalized handoff section when sharing the normalized dataset. Keep the source caches and original denominator API response locally or in a backup.

## OpenAlex references

- OpenAlex Works API: https://api.openalex.org/works
- OpenAlex API search: https://help.openalex.org/api/searching/
- OpenAlex pagination: https://help.openalex.org/api/paging/
- OpenAlex topics: https://help.openalex.org/data/topics/

- OpenAlex grouping and pagination: https://help.openalex.org/api/grouping/

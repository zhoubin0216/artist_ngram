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


## Movement-level analysis and final reporting policy

The agreed movement-level analysis keeps artist-linked documents as the primary indicator and movement-term mentions as an auxiliary indicator. An optional Work-ID union is exploratory and includes movement keyword matches whose meaning has not been fully disambiguated.

### Indicators

For movement m and publication year y, define:

- A: distinct retained artist-linked Work IDs. Map cleaned artist matches to movement memberships using `outputs/artist_dictionary.csv`, then deduplicate within movement and year. A work mentioning two artists in the same movement counts once.
- B: distinct Work IDs whose title or available abstract matches a configured movement term under the same corpus, language, topic and year filters. These are keyword mentions, not confirmed movement identities or guaranteed primary discussion.
- O: the intersection of A and B.
- U: the union, with U = A + B - O.

For example, the Impressionism result for 1900 combines documents mentioning dictionary-listed Impressionist artists and documents mentioning the configured Impressionism term. A document appearing through both paths counts once in U.

Use A as the main artist-linked scholarly visibility series. Show B separately as movement-term visibility. Use U only as an explicitly labeled exploratory combined series; it must not be described as a fully disambiguated count of papers about the movement.

The same work may belong to multiple movements. Do not sum movement counts as globally unique works. These document-union indicators use full membership inclusion, not fractional artist-membership weights. If a separate fractional analysis is needed, label it as a different indicator. Dictionary affiliations are artist-level memberships and are not dated movement memberships.

### Generate the agreed keyword-proxy table from existing data

The current `openalex_movements.py` can generate the agreed A/B/O/U table without using automatic classification. Run from the repository root after the complete movement collection is available:

```powershell
.\.venv\Scripts\python.exe scripts/openalex_movements.py build --allow-unreviewed --output results/openalex_movements_keyword_proxy
```

Do not supply `--reviews` or `--use-automatic-labels` for this operational definition. This keeps all locally verified movement keyword matches, preserves the original cleaned artist decisions, and explicitly records that direct matches are unreviewed. It uses existing local files and does not call the API or download full text.

The current local project already contains these output files. The command creates the directory if needed and overwrites these same-named files when rerun. Use a different --output directory to preserve a previous snapshot:

```text
results/openalex_movements_keyword_proxy/
  movement_year_union.csv
  movement_summary.json
  movement_work_union.jsonl
  direct_match_exclusions.csv
```

The two annual-table keys are `movement_id` and `year`. The table contains all dictionary movements and every year 1800-2019; 15 movements yield 3,300 rows.

| Field | Final analysis role |
| --- | --- |
| artist_linked_works_count | Primary indicator A |
| direct_movement_works_count | Auxiliary keyword indicator B |
| overlap_works_count | Intersection O |
| union_works_count | Exploratory combined indicator U |
| denominator_works | Same-scope annual art corpus size |
| movement_works_per_10000 | Normalized exploratory union, U / denominator * 10,000 |

The existing script outputs a normalized UNION only. In the integration/analysis stage, derive primary and auxiliary normalized series separately:

```text
artist_linked_per_10000 = artist_linked_works_count / denominator_works * 10000
movement_term_per_10000 = direct_movement_works_count / denominator_works * 10000
```

Use an unavailable value when the denominator is zero. Do not sum per-artist rates to obtain a movement document rate; first deduplicate artist-linked Work IDs within the movement, then divide that count by the denominator.

The previously generated `openalex_movements_auto_broad` and `openalex_movements_auto_strict` are sensitivity experiments. Broad excludes records labeled irrelevant by automatic rules, so it is not the completely unfiltered keyword proxy. Strict also excludes automatic uncertain records. Neither is human-verified ground truth, and neither replaces the final primary/auxiliary policy above.

### How to use the movement-term series

1. Plot A and B separately, both as raw counts and per-10,000 rates. Retain the denominator series for interpreting early sparse years.
2. Examine overlap. O / B describes the share of keyword-matched documents that also mention a dictionary-listed artist in the movement. It is not a precision estimate: movement theory papers may legitimately name no artists.
3. Compare annual or decade-level trends. Report the time window and missing/zero handling. Correlation can reflect shared documents, overall corpus growth and common trends; it does not establish causal influence or validate individual matches.
4. For a less mechanically overlapping comparison, compare A - O and B - O as separate series. Detrending or differencing can be considered alongside raw-rate correlations; no single correlation removes all bias.
5. Inspect periods where B changes sharply without a similar change in A. Such periods may represent different meanings or genuine movement-level discussion. Do not delete documents solely because their series differ.
6. For clustering, begin with the primary A series and treat B as an auxiliary feature or a separate robustness analysis. Define whether clusters reflect magnitude or curve shape, and handle all-zero sequences separately.

Realism, Symbolism, Romanticism, Expressionism, Futurism and Dada can have other meanings or media contexts. The 1213 topic filter includes performing arts as well as visual arts. Specific topic labels and context screens provide additional evidence but cannot guarantee movement identity. Automated uncertain labels indicate insufficient rule evidence, not proven irrelevance.

### Verified current movement snapshot

The keyword-proxy table has 3,300 rows. Summed across movement-work memberships, A is 20,946, B is 30,973, O is 1,439 and U is 50,480. These totals satisfy U = A + B - O. They are not globally distinct document totals because works may belong to multiple movements. Automatic classification is disabled and no direct keyword matches are excluded in this version.

A also remains a full-name mention proxy based on recorded artist cleaning decisions; calling it the primary indicator does not mean every artist-linked match has been manually identity-verified. For O / B and analogous proportions, report an unavailable value when the denominator is zero. When combining years, aggregate counts first and then divide rather than averaging annual rates without an explicit weighting rule.

### Remaining work before the movement handoff

- The current keyword-proxy outputs have been generated and checked. If inputs change, rerun the build and check the summary, collection scope and all 3,300 movement-year keys.
- Verify U = A + B - O for every row, and retain the union detail locally for Work-ID audit.
- Derive and document the separate normalized A and B series in the integration/analysis workflow; the current movement script does not yet write those two rate columns.
- Deliver the annual CSV, summary, movement term list and artist dictionary to the integration contributor.
- Analyze common source-coverage years, separate zero counts from unavailable observations, and document ambiguous movement terms in the final report.
- Complete the team's storage/query performance experiments, cross-source plots and machine-learning evaluation separately; producing these movement tables alone does not complete the entire project.

## Getty Provenance pilot and Task 3 benchmark

This branch also contains a reproducible Getty Provenance Index pilot and the
first local single-node Spark performance benchmark. The Getty pilot is kept
separate from the OpenAlex and Google Books pipelines above.

### Completed scope

- Selected a representative 25-artist pilot from the project artist dictionary.
- Queried Getty's official Provenance SPARQL endpoint with saved query templates.
- Matched artists using names, life dates, ULAN evidence, and Getty person
  records. A ULAN identifier is supporting evidence, not an assumed direct link
  to a Provenance person URI.
- Preserved raw endpoint responses locally and wrote reviewable CSV plus Spark
  Parquet outputs.
- Implemented four Spark queries and comparisons for text versus Parquet,
  unpartitioned versus year-partitioned Parquet, and standard versus broadcast
  joins.
- Added deterministic row-count/checksum validation and automated tests.

The pilot produced 25 mapping rows: 23 automatic matches and 2 manual-review
records. Salvador Dalí is unmatched in the current endpoint response; Umberto
Boccioni is retained as a low-confidence candidate. The event table contains
5,540 rows: 2,805 confirmed sales and 2,735 catalog entries.

### Data rules

- `confirmed_sale` requires Getty acquisition/title-transfer evidence; a catalog
  appearance alone is never treated as a sale.
- Catalog entries and confirmed transactions remain separate in every aggregate.
- Prices in different currencies are not summed together.
- Missing values remain empty and uncertain mappings are marked for review.
- Raw API responses are ignored by Git; cleaned pilot outputs are versioned.

The field definitions, provenance caveats, and matching policy are documented in
`docs/getty_data_dictionary.md`.

### Environment and reproducibility

The recorded run used this Windows machine because WSL2 Ubuntu was not installed.
It used Python 3.9, Java 8, PySpark 3.5.3, and Spark `local[4]`. A Windows
Unicode-path compatibility helper copies the Spark/Python worker launcher to a
temporary ASCII-only path before starting Spark. Benchmark storage fixtures are
created outside timed sections with pandas/PyArrow; Spark reads exact file
manifests, and the partitioned Q2 query reads only the requested year
partitions. No unverified `winutils` binary is required.

Create the environment and run the pipelines from the repository root:

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\collect_environment.py

# Smoke test, then the complete 25-artist pilot
.\.venv\Scripts\python.exe scripts\getty\run_pipeline.py --limit 5
.\.venv\Scripts\python.exe scripts\getty\run_pipeline.py --limit 25

# Task 3 benchmark and tests
.\.venv\Scripts\python.exe scripts\benchmark\run_benchmark.py --config configs\benchmark.yaml
.\.venv\Scripts\python.exe scripts\benchmark\run_openalex_benchmark.py --config configs\openalex_benchmark.yaml
.\.venv\Scripts\python.exe scripts\benchmark\run_integrated_benchmark.py --config configs\integrated_benchmark.yaml
.\.venv\Scripts\python.exe -m pytest -q
```

Important outputs:

- `data/getty/sample_artists.csv`
- `data/getty/getty_artist_mapping.csv`
- `data/getty/getty_events.csv` and `data/getty/getty_events.parquet`
- `data/getty/getty_artist_year.csv` and `data/getty/getty_artist_year.parquet`
- `data/getty/getty_pilot_summary.json`
- `outputs/benchmark_results.csv`
- `outputs/benchmark_summary.md`
- `outputs/benchmark_chart.png`
- `outputs/openalex_benchmark_results.csv`
- `outputs/openalex_benchmark_summary.md`
- `outputs/openalex_benchmark_chart.png`
- `outputs/openalex_query_validation.json`
- `outputs/integrated_benchmark_results.csv`
- `outputs/integrated_benchmark_summary.md`
- `outputs/integrated_benchmark_chart.png`
- `outputs/integrated_query_validation.json`
- `outputs/environment_report.json`

### Current benchmark snapshot

The engineering stress test uses 554,000 explicitly synthetic rows expanded from
the cleaned Getty pilot. It is benchmark-only data, not research evidence. On
the recorded machine, the generated storage sizes were 281.7 MB for CSV,
456.3 MB for JSON, 34.7 MB for unpartitioned Parquet, and 23.0 MB for
year-partitioned Parquet. For the year-filter query, median time was 0.390 s for
partitioned Parquet and 0.546 s for unpartitioned Parquet. On this small local
join, broadcast was slower (1.047 s versus 0.700 s), illustrating that an
optimization must be measured rather than assumed. See
`outputs/benchmark_summary.md` for all query medians and validation checks.

HDFS was not configured, so the benchmark deliberately reports local filesystem
results only. Network/API time is excluded from every Spark comparison.

The separate OpenAlex benchmark uses the real 301,400-row normalized annual
table without synthetic duplication. Unpartitioned Parquet occupied 120,709
bytes versus 14,943,314 bytes for CSV. Year partitioning improved the selective
1900 query from 0.129911 s to 0.122485 s, but its 220 small files made full-range
queries slower. The standard movement join was also faster than broadcast in
this local run (0.325210 s versus 0.406464 s). Full methodology and the boundary
between engineering aggregates and official movement indicators are documented
in `docs/task3_performance.md`.

The comprehensive three-source layer additionally covers Ngram as a core input.
Artist, movement and period queries each return Ngram, OpenAlex, Getty and a
non-additive cross-source summary. Movement queries contain both member-artist
aggregates and independent Ngram/OpenAlex movement series. See
`docs/task3_comprehensive.md` for the final data model, commands, validation and
performance results.

### Remaining work

- Manually review the two uncertain/unmatched Getty records before treating them
  as resolved identities.
- Add robust endpoint pagination/retry/checkpoint handling before expanding from
  25 to all approximately 1,370 artists.
- Reassess query coverage and Getty data-model variations during the full run.
- Repeat both fixed benchmarks only if final input tables or Spark configuration
  change, and retain the input hashes with the new results.
- Add HDFS only if a correctly configured local HDFS environment becomes
  available; do not mix results from another computer.

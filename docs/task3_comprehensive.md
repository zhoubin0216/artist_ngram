# Task 3 comprehensive engineering plan and results

## 1. Goal and completed scope

Task 3 implements three query families over Ngram, OpenAlex and Getty. Every
query returns the three sources independently and a fourth cross-source summary.
The summary aligns metrics by entity and year. It never adds values with
different units.

The implementation covers:

1. all available annual data for one artist;
2. all available annual data for one movement, including both member-artist
   aggregation and independently collected movement data;
3. all artist and movement data for one year or a year range;
4. CSV, JSON Lines, unpartitioned Parquet and year-partitioned Parquet;
5. standard and broadcast Spark joins;
6. one warm-up and three measured runs with median timing and deterministic
   result checksums.

All final measurements were made on the Windows computer documented in
`docs/environment.md`. API access and fixture generation are outside the timed
region. HDFS is not included because it is not configured on this machine.

## 2. Design choices

### Common metric table

The three sources use different units and sparsity rules. The integration layer
therefore uses a metric-long schema:

```text
source
entity_type
entity_id
entity_name
year
series_kind
metric
value
query_term
n
availability
```

The current build contains 1,409,050 metric rows:

| Source | Metric rows |
|---|---:|
| Ngram | 157,988 |
| OpenAlex | 1,238,600 |
| Getty | 12,462 |

There are 1,362,519 artist metric rows and 46,531 movement metric rows. The
artist dictionary contains 1,370 artists, and splitting multi-valued movement
fields creates 1,533 unique artist-movement relations.

### Cross-source summary

The summary pivots metrics into source-prefixed columns such as
`ngram__match_count`, `openalex__works_count` and
`getty__confirmed_sale_count`. No cross-source total is calculated. Ngram word
frequency, OpenAlex document counts and Getty provenance records are not
commensurate quantities.

### Missing values

- OpenAlex's complete artist-year grid contains meaningful zero values.
- Ngram missing rows remain null. They are not converted to zero.
- Getty missing artist-years remain null.
- Getty has no independently collected movement-term series. Its direct
  movement fields remain unavailable instead of being filled with zero.

### Artist-to-movement aggregation

Every artist is assigned in full to each dictionary-listed movement. A
multi-movement artist therefore contributes to each movement. This implements
the requested member-artist sum, but totals across movements can double-count an
artist or record. No fractional weighting is applied.

For Ngram, the member-artist series sums `match_count` and `volume_count`. For
OpenAlex, it sums artist-level work counts and recalculates the per-10,000 rate
with the common annual denominator. For Getty, confirmed sales, catalog entries
and total records remain separate.

### Independent movement data

Ngram's `movement_year_counts.jsonl` is retained as
`direct_movement_term`. OpenAlex's official A/B/O/U table is retained as
`direct_official_union`. These remain separate from member-artist sums. The
OpenAlex member sum can count one Work ID more than once when it mentions
multiple artists in the movement; the official A series deduplicates Work IDs
within a movement.

## 3. Query interface

Each command writes `ngram.csv`, `openalex.csv`, `getty.csv`, `summary.csv` and
`manifest.json` under the ignored `outputs/task3_query_results/` directory.

### Artist query

```powershell
.\.venv\Scripts\python.exe scripts\benchmark\query_integrated.py artist `
  --artist-id Q296 --year-start 1800 --year-end 2019 --label artist_Q296
```

The verified Claude Monet query returned:

| Output | Rows |
|---|---:|
| Ngram | 290 |
| OpenAlex | 880 |
| Getty | 204 |
| Cross-source summary | 220 |

The summary has one row per year. Sparse Ngram and Getty observations remain
empty beside OpenAlex's complete annual grid.

### Movement query

```powershell
.\.venv\Scripts\python.exe scripts\benchmark\query_integrated.py movement `
  --movement-id M05 --year-start 1850 --year-end 1920 `
  --label movement_M05_1850_1920
```

The verified Impressionism query returned:

| Output | Rows |
|---|---:|
| Ngram | 246 |
| OpenAlex | 710 |
| Getty | 168 |
| Cross-source summary | 142 |

The summary contains 71 years and two rows per year: one
`artist_membership_sum` row and one `direct_movement` row.

### One-year query

```powershell
.\.venv\Scripts\python.exe scripts\benchmark\query_integrated.py period `
  --year-start 1900 --year-end 1900 --label period_1900
```

The verified 1900 query returned 666 Ngram metric rows, 5,630 OpenAlex metric
rows, 93 Getty metric rows and 1,400 summary rows. Entity IDs and canonical
dictionary names are used as the cross-source identity; capitalization variants
cannot create duplicate summary rows.

### Year-range query

```powershell
.\.venv\Scripts\python.exe scripts\benchmark\query_integrated.py period `
  --year-start 1850 --year-end 1920 --label period_1850_1920
```

The verified range query returned:

| Output | Rows |
|---|---:|
| Ngram | 28,648 |
| OpenAlex | 399,730 |
| Getty | 6,645 |
| Cross-source summary | 99,400 |

Every generated manifest records a checksum for each independent source and the
summary.

## 4. Performance experiment

Run the complete benchmark with:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark\run_integrated_benchmark.py `
  --config configs\integrated_benchmark.yaml
```

Fixed configuration:

- PySpark 3.5.3, `local[4]`;
- 4 GB driver and executor memory;
- 8 shuffle partitions;
- adaptive execution disabled;
- automatic broadcast disabled;
- one warm-up and three measured runs;
- median wall-clock time reported.

### Storage size

| Format | Bytes |
|---|---:|
| CSV | 135,371,351 |
| JSON Lines | 334,530,123 |
| Unpartitioned Parquet | 727,251 |
| Year-partitioned Parquet | 13,637,595 |

Unpartitioned Parquet is highly compact because the metric-long table contains
many repeated categorical values and zero-filled OpenAlex annual measures. The
220 year partitions add substantial small-file overhead.

### Median query time

| Format | Artist Q296 | Movement M05, 1850–1920 | Period 1900 |
|---|---:|---:|---:|
| CSV | 2.536218 s | 2.451271 s | 2.559218 s |
| JSON Lines | 2.556601 s | 2.399750 s | 2.826366 s |
| Unpartitioned Parquet | 0.871527 s | 0.830156 s | 0.746869 s |
| Year-partitioned Parquet | 1.637942 s | 1.083737 s | 0.761091 s |

Year partitioning read one 60,810-byte file for the selective one-year query,
but was slightly slower than unpartitioned Parquet in this run. It was also
slower for the 71-year movement range and the full 220-year artist query because
file-opening overhead outweighed pruning benefits.

### Join strategy

The join benchmark rebuilds additive artist-to-movement aggregates from the
artist metric rows and the 1,533-row bridge:

| Strategy | Median | Output rows |
|---|---:|---:|
| Standard join | 0.432874 s | 15,375 |
| Broadcast join | 0.355631 s | 15,375 |

Broadcast was about 18% faster in this integrated workload. This differs from
the earlier OpenAlex-only benchmark, where broadcast was slower. Join strategy
therefore depends on the input layout and must be measured for the final query,
not selected from a general rule.

All four storage formats produced identical row counts and checksums for each
query. Standard and broadcast joins also produced identical results.

## 5. Files and reproducibility

Implementation:

```text
configs/integrated_benchmark.yaml
scripts/benchmark/build_integrated_metrics.py
scripts/benchmark/integrated_queries.py
scripts/benchmark/query_integrated.py
scripts/benchmark/run_integrated_benchmark.py
tests/test_integrated_queries.py
```

Versioned outputs:

```text
outputs/integrated_benchmark_results.csv
outputs/integrated_benchmark_summary.md
outputs/integrated_benchmark_chart.png
outputs/integrated_query_validation.json
```

Generated CSV, JSON and Parquet fixtures remain under ignored
`data/benchmark/integrated/`. Query result extracts remain under ignored
`outputs/task3_query_results/`. Neither directory is research source data.

Run the focused tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_getty_pipeline.py `
  tests\test_spark_queries.py `
  tests\test_openalex_benchmark.py `
  tests\test_integrated_queries.py
```

The current focused suite passes seven tests. The repository's Ngram downloader
tests still cannot be collected on Windows because those download scripts import
the Unix-only `fcntl` module. This does not affect reading the committed Ngram
outputs or running Task 3.

## 6. Remaining limitations

- HDFS is not installed, so no local-filesystem/HDFS comparison is reported.
- Stable per-query shuffle byte counts are unavailable through PySpark 3.5's
  public Python API. Executed physical plans are retained locally for audit.
- Getty provides no independent movement-term dataset.
- Getty mapping attempted all 1,370 artists, but only 495 passed the automatic
  acceptance threshold. Unmatched and review-required artists remain missing.
- Getty supplies no usable year for 56,708 of 79,715 events, so year-based
  queries use only the 23,007 dated events and do not infer missing dates.
- Ngram direct movement counts measure exact terms; ambiguous terms can include
  non-art meanings.
- OpenAlex member-artist sums and official Work-ID unions answer different
  questions and must remain separately labelled.
- Cross-source summaries support aligned comparison. They do not create a single
  composite visibility score.

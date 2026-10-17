# Task 3: engineering performance analysis

## Scope

Task 3 measures storage and Spark execution strategies. It does not use query
speed to make an art-historical claim. All final measurements in this repository
were made on the Windows computer documented in `docs/environment.md`; results
from other machines are not mixed into the tables.

There are two complementary workloads:

1. The Getty workload uses 79,715 cleaned events from the full-dictionary run,
   expanded with seven labelled copies to 558,005 rows. Every copied event has a
   unique benchmark identifier and is explicitly marked synthetic. It supplies
   the four required event queries, including separate confirmed-sale and
   catalog-entry counts.
2. The OpenAlex workload uses the real 301,400-row normalized artist-year table
   without duplication. It verifies that the engineering conclusions also hold
   on the team's latest annual research table.

API/network time and fixture-generation time are excluded from both workloads.

## Fixed Spark configuration

- PySpark 3.5.3, `local[4]`
- 4 GB driver and executor memory
- 8 shuffle partitions and default parallelism 8
- adaptive execution disabled
- automatic broadcast disabled so the standard and explicit broadcast plans are
  genuinely different
- one warm-up and three measured runs per scenario; the median is reported

The executed plans confirm a `SortMergeJoin` for the standard OpenAlex Q3 and a
`BroadcastHashJoin` for the explicit broadcast case. Plans are generated under
the ignored `outputs/benchmark_plans/` directory.

## Queries

### Getty event workload

- Q1: one artist's annual event records
- Q2: all events in a year range
- Q3: movement/year aggregation after joining the artist dimension
- Q4: artist/year confirmed-sale and catalog-entry counts kept separate

### OpenAlex annual workload

- Q1: `Q296` (Claude Monet), 1800–2019
- Q2: all 1,370 artists in 1900, ordered by count
- Q3: artist/year metrics joined to the dictionary and grouped by movement/year
- Q4 correctness query: document-type distribution from the aggregate type table

OpenAlex Q3 is named `artist_membership_works_sum`. It may double-count one Work
ID when the work mentions multiple artists in the same movement. It is suitable
for testing the requested join and aggregation, but it does not replace the
official Work-ID-deduplicated A/B/O/U movement table. The official M05 table is
validated separately for 1850–1920, including the identity `U = A + B - O`.

The type table supports the total number of artist-work matches (15,679), not a
global distinct Work-ID total. The latter is reported as unavailable rather than
guessed; it requires the locally held artist work-level JSONL.

## Storage and execution comparisons

Both workloads compare CSV, JSON Lines, unpartitioned Parquet, and Parquet
partitioned by year. Identical schemas and query logic are used, and every
scenario records input/output rows plus a deterministic result checksum.

On Windows, pandas/PyArrow creates equivalent fixtures outside the timed region.
Spark performs every measured read, filter, join, and aggregation. Exact file
manifests avoid dependence on an unverified `winutils.exe`; for partitioned Q2,
the manifest selects only the requested year file.

OpenAlex's real table demonstrates the small-file tradeoff clearly:

- CSV: 14,943,314 bytes
- JSON Lines: 60,747,007 bytes
- unpartitioned Parquet: 120,709 bytes
- year-partitioned Parquet: 8,777,054 bytes across 220 files
- Q2 median: 0.129911 s unpartitioned versus 0.122485 s partitioned
- full Q1 scan: 0.139487 s unpartitioned versus 0.542710 s partitioned
- Q3 standard join: 0.325210 s; broadcast join: 0.406464 s

Thus year partitioning helped the selective single-year query but substantially
hurt full-range scans because of 220 small files. Broadcast was also slower for
this small local dimension. These measurements should not be generalized to a
different machine or cluster without rerunning the same protocol.

The rerun Getty workload produced these storage sizes and representative
medians:

- CSV: 281,015,324 bytes
- JSON Lines: 457,821,378 bytes
- unpartitioned Parquet: 64,744,426 bytes
- year-partitioned Parquet: 56,034,637 bytes
- Q2 median: 0.397954 s unpartitioned versus 0.366209 s partitioned
- Q3 standard join: 0.613597 s; broadcast join: 0.814653 s

The synthetic multiplier changed from 100 to 7 so the engineering input stayed
near the original 554,000-row scale after replacing the pilot with full-run
Getty data. Synthetic copies are never presented as additional research
observations.

Shuffle read/write columns remain empty because PySpark 3.5 has no stable public
Python API for per-query task shuffle metrics. The absence is explicit rather
than an invented zero. HDFS is not compared because it is not configured.

## Reproduce

From the repository root in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark\run_benchmark.py --config configs\benchmark.yaml
.\.venv\Scripts\python.exe scripts\benchmark\run_openalex_benchmark.py --config configs\openalex_benchmark.yaml
.\.venv\Scripts\python.exe -m pytest -q tests\test_getty_pipeline.py tests\test_spark_queries.py tests\test_openalex_benchmark.py
```

Outputs are `outputs/benchmark_*` for Getty and
`outputs/openalex_benchmark_*` plus `outputs/openalex_query_validation.json` for
OpenAlex. Generated format copies remain under ignored `data/benchmark/`.

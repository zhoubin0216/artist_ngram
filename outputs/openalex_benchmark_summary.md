# OpenAlex benchmark summary

All timings were measured on this Windows computer. API and fixture-generation time is excluded.
The input is the real 301,400-row OpenAlex annual table; no synthetic duplication is used.

## Fixed configuration

- Spark version: 3.5.3
- Master: `local[4]`
- Driver memory: `4g`
- Shuffle partitions: `8`
- Adaptive execution: `False`
- Warm-up runs: `1`
- Measured runs: `3`

## Validation snapshot

- Annual rows: 301400; artists: 1370; retained artist-work matches: 15679.
- Q1 Monet rows: 220; Q2 1900 rows: 1370; M05 1850-1920 rows: 71.
- The type table sums to 15,679 artist-work matches. A global distinct Work-ID count is deliberately left unavailable because the aggregate table cannot support it.

## Findings

- Unpartitioned Parquet used 99.2% less disk space than CSV.
- For Q2 (year 1900), year-partitioned Parquet was 5.7% faster than unpartitioned Parquet.
- Broadcast join was 25.0% slower than the standard join on this machine.
- Q3 is an artist-membership sum for engineering comparison, not the official Work-ID-deduplicated OpenAlex movement series.

## Storage formats

| Format | Query | Input rows | Files read | Bytes read | Total storage bytes | Median seconds | Output rows | Validated |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| csv | Q1 | 301400 | 1 | 14943314 | 14943314 | 0.210834 | 220 | yes |
| csv | Q2 | 301400 | 1 | 14943314 | 14943314 | 0.187482 | 1370 | yes |
| csv | Q3 | 301400 | 1 | 14943314 | 14943314 | 0.42247 | 3300 | yes |
| json | Q1 | 301400 | 1 | 60747007 | 60747007 | 0.188965 | 220 | yes |
| json | Q2 | 301400 | 1 | 60747007 | 60747007 | 0.188125 | 1370 | yes |
| json | Q3 | 301400 | 1 | 60747007 | 60747007 | 0.422723 | 3300 | yes |
| parquet_unpartitioned | Q1 | 301400 | 1 | 120709 | 120709 | 0.139487 | 220 | yes |
| parquet_unpartitioned | Q2 | 301400 | 1 | 120709 | 120709 | 0.129911 | 1370 | yes |
| parquet_unpartitioned | Q3 | 301400 | 1 | 120709 | 120709 | 0.326175 | 3300 | yes |
| parquet_partitioned | Q1 | 301400 | 220 | 8777054 | 8777054 | 0.54271 | 220 | yes |
| parquet_partitioned | Q2 | 301400 | 1 | 39686 | 8777054 | 0.122485 | 1370 | yes |
| parquet_partitioned | Q3 | 301400 | 220 | 8777054 | 8777054 | 0.684664 | 3300 | yes |

## Join strategies

| Strategy | Query | Median seconds | Output rows | Validated |
|---|---:|---:|---:|---|
| standard | Q3 | 0.32521 | 3300 | yes |
| broadcast | Q3 | 0.406464 | 3300 | yes |

Shuffle read/write bytes are blank in the CSV because PySpark 3.5 does not expose stable per-query task metrics through a public Python API. Executed plans are stored in the ignored benchmark plan directory.
HDFS was not tested because it is not configured on this machine.

# Benchmark summary

All timings were measured on this Windows computer. API request time is excluded.
The benchmark input is explicitly synthetic: cleaned pilot rows are duplicated with unique benchmark IDs.
Storage fixtures and exact-file manifests were generated once with pandas/PyArrow outside all timings; Spark performs every measured read and query.
On Windows, the year-partitioned Q2 manifest selects only requested year directories, equivalent to partition pruning without Hadoop directory-list JNI.

## Fixed configuration

- Spark version: 3.5.3
- Master: `local[4]`
- Driver memory: `4g`
- Shuffle partitions: `8`
- Adaptive execution: `False`
- Synthetic multiplier: `100`
- Warm-up runs: `1`
- Measured runs: `3`

## Findings

- Unpartitioned Parquet used 87.7% less disk space than CSV; year-partitioned Parquet used 91.8% less.
- For Q2 (1800-1950), year-partitioned Parquet was 28.6% faster than unpartitioned Parquet.
- Broadcast join was 49.6% slower than the standard join in this local pilot benchmark; broadcasting is not automatically beneficial at this scale.
- These are engineering measurements on synthetic duplicates, not additional historical observations.

## Storage formats

| Format | Query | Input rows | Storage bytes | Median seconds | Output rows | Validated |
|---|---:|---:|---:|---:|---:|---|
| csv | Q1 | 554000 | 281675633 | 0.566531 | 96 | yes |
| csv | Q2 | 554000 | 281675633 | 0.546127 | 218500 | yes |
| csv | Q3 | 554000 | 281675633 | 0.872307 | 477 | yes |
| csv | Q4 | 554000 | 281675633 | 0.437272 | 641 | yes |
| json | Q1 | 554000 | 456276600 | 0.381271 | 96 | yes |
| json | Q2 | 554000 | 456276600 | 0.529136 | 218500 | yes |
| json | Q3 | 554000 | 456276600 | 0.854754 | 477 | yes |
| json | Q4 | 554000 | 456276600 | 0.43732 | 641 | yes |
| parquet_unpartitioned | Q1 | 554000 | 34732392 | 0.309793 | 96 | yes |
| parquet_unpartitioned | Q2 | 554000 | 34732392 | 0.545889 | 218500 | yes |
| parquet_unpartitioned | Q3 | 554000 | 34732392 | 0.687353 | 477 | yes |
| parquet_unpartitioned | Q4 | 554000 | 34732392 | 0.187134 | 641 | yes |
| parquet_partitioned | Q1 | 554000 | 23034285 | 0.482685 | 96 | yes |
| parquet_partitioned | Q2 | 554000 | 23034285 | 0.389766 | 218500 | yes |
| parquet_partitioned | Q3 | 554000 | 23034285 | 0.801676 | 477 | yes |
| parquet_partitioned | Q4 | 554000 | 23034285 | 0.375095 | 641 | yes |

## Join strategies

| Strategy | Query | Median seconds | Output rows | Validated |
|---|---:|---:|---:|---|
| standard | Q3 | 0.699785 | 477 | yes |
| broadcast | Q3 | 1.047018 | 477 | yes |

Shuffle read/write bytes are left blank in the CSV because PySpark 3.5 does not expose stable per-query task metrics through a public Python API. Physical plans are saved separately for audit.
HDFS was not tested because this machine does not have an HDFS environment configured.

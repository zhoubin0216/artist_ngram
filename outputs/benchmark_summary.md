# Benchmark summary

All timings were measured on this Windows computer. API request time is excluded.
The benchmark input is explicitly synthetic: cleaned Getty rows are duplicated with unique benchmark IDs.
Storage fixtures and exact-file manifests were generated once with pandas/PyArrow outside all timings; Spark performs every measured read and query.
On Windows, the year-partitioned Q2 manifest selects only requested year directories, equivalent to partition pruning without Hadoop directory-list JNI.

## Fixed configuration

- Spark version: 3.5.3
- Master: `local[4]`
- Driver memory: `4g`
- Shuffle partitions: `8`
- Adaptive execution: `False`
- Synthetic multiplier: `7`
- Warm-up runs: `1`
- Measured runs: `3`

## Findings

- Unpartitioned Parquet used 77.0% less disk space than CSV; year-partitioned Parquet used 80.1% less.
- For Q2 (1800-1950), year-partitioned Parquet was 8.0% faster than unpartitioned Parquet.
- Broadcast join was 32.8% slower than the standard join in this local benchmark; broadcasting is not automatically beneficial at every scale.
- These are engineering measurements on synthetic duplicates, not additional historical observations.

## Storage formats

| Format | Query | Input rows | Storage bytes | Median seconds | Output rows | Validated |
|---|---:|---:|---:|---:|---:|---|
| csv | Q1 | 558005 | 281015324 | 0.551536 | 2 | yes |
| csv | Q2 | 558005 | 281015324 | 0.528683 | 135282 | yes |
| csv | Q3 | 558005 | 281015324 | 0.838893 | 878 | yes |
| csv | Q4 | 558005 | 281015324 | 0.485672 | 3709 | yes |
| json | Q1 | 558005 | 457821378 | 0.324372 | 2 | yes |
| json | Q2 | 558005 | 457821378 | 0.446745 | 135282 | yes |
| json | Q3 | 558005 | 457821378 | 0.792377 | 878 | yes |
| json | Q4 | 558005 | 457821378 | 0.434877 | 3709 | yes |
| parquet_unpartitioned | Q1 | 558005 | 64744426 | 0.26357 | 2 | yes |
| parquet_unpartitioned | Q2 | 558005 | 64744426 | 0.397954 | 135282 | yes |
| parquet_unpartitioned | Q3 | 558005 | 64744426 | 0.657313 | 878 | yes |
| parquet_unpartitioned | Q4 | 558005 | 64744426 | 0.187715 | 3709 | yes |
| parquet_partitioned | Q1 | 558005 | 56034637 | 0.436753 | 2 | yes |
| parquet_partitioned | Q2 | 558005 | 56034637 | 0.366209 | 135282 | yes |
| parquet_partitioned | Q3 | 558005 | 56034637 | 0.76263 | 878 | yes |
| parquet_partitioned | Q4 | 558005 | 56034637 | 0.388451 | 3709 | yes |

## Join strategies

| Strategy | Query | Median seconds | Output rows | Validated |
|---|---:|---:|---:|---|
| standard | Q3 | 0.613597 | 878 | yes |
| broadcast | Q3 | 0.814653 | 878 | yes |

Shuffle read/write bytes are left blank in the CSV because PySpark 3.5 does not expose stable per-query task metrics through a public Python API. Physical plans are saved separately for audit.
HDFS was not tested because this machine does not have an HDFS environment configured.

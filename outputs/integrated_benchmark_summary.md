# Three-source benchmark summary

The benchmark covers Ngram, OpenAlex and Getty independently and produces a non-additive cross-source summary for every query.
All measurements were made on the documented Windows computer. Fixture generation and network access are excluded.

## Configuration

- Spark: 3.5.3, `local[4]`, driver memory `4g`
- Integrated metric rows: 1409050
- Warm-up runs: 1
- Measured runs: 3
- Cross-source metrics are aligned by keys and never added across units.
- Missing Ngram years remain null; Getty has no direct movement-term series.

## Storage results

| Format | Query | Files read | Bytes read | Total bytes | Median seconds | Output rows |
|---|---:|---:|---:|---:|---:|---:|
| csv | artist | 1 | 135371351 | 135371351 | 2.536218 | 1594 |
| csv | movement | 1 | 135371351 | 135371351 | 2.451271 | 1266 |
| csv | period | 1 | 135371351 | 135371351 | 2.559218 | 7789 |
| json | artist | 1 | 334530123 | 334530123 | 2.556601 | 1594 |
| json | movement | 1 | 334530123 | 334530123 | 2.39975 | 1266 |
| json | period | 1 | 334530123 | 334530123 | 2.826366 | 7789 |
| parquet_unpartitioned | artist | 1 | 727251 | 727251 | 0.871527 | 1594 |
| parquet_unpartitioned | movement | 1 | 727251 | 727251 | 0.830156 | 1266 |
| parquet_unpartitioned | period | 1 | 727251 | 727251 | 0.746869 | 7789 |
| parquet_partitioned | artist | 220 | 13637595 | 13637595 | 1.637942 | 1594 |
| parquet_partitioned | movement | 71 | 4070179 | 13637595 | 1.083737 | 1266 |
| parquet_partitioned | period | 1 | 60810 | 13637595 | 0.761091 | 7789 |

## Join strategy

| Strategy | Median seconds | Output rows |
|---|---:|---:|
| standard | 0.432874 | 15375 |
| broadcast | 0.355631 | 15375 |

Every storage format and both join strategies produced identical row counts and checksums.
Shuffle bytes remain unavailable because PySpark 3.5 has no stable public per-query Python metrics API. HDFS is not configured on this machine.

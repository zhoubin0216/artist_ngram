# Three-source benchmark summary

The benchmark covers Ngram, OpenAlex and Getty independently and produces a non-additive cross-source summary for every query.
All measurements were made on the documented Windows computer. Fixture generation and network access are excluded.

## Configuration

- Spark: 3.5.3, `local[4]`, driver memory `4g`
- Integrated metric rows: 1399840
- Warm-up runs: 1
- Measured runs: 3
- Cross-source metrics are aligned by keys and never added across units.
- Missing Ngram years remain null; Getty has no direct movement-term series.

## Storage results

| Format | Query | Files read | Bytes read | Total bytes | Median seconds | Output rows |
|---|---:|---:|---:|---:|---:|---:|
| csv | artist | 1 | 134485879 | 134485879 | 2.627414 | 1573 |
| csv | movement | 1 | 134485879 | 134485879 | 2.395323 | 1194 |
| csv | period | 1 | 134485879 | 134485879 | 2.544367 | 7720 |
| json | artist | 1 | 332336831 | 332336831 | 2.547306 | 1573 |
| json | movement | 1 | 332336831 | 332336831 | 2.416618 | 1194 |
| json | period | 1 | 332336831 | 332336831 | 2.79041 | 7720 |
| parquet_unpartitioned | artist | 1 | 703911 | 703911 | 0.900818 | 1573 |
| parquet_unpartitioned | movement | 1 | 703911 | 703911 | 0.829913 | 1194 |
| parquet_unpartitioned | period | 1 | 703911 | 703911 | 0.760498 | 7720 |
| parquet_partitioned | artist | 220 | 13680765 | 13680765 | 1.618993 | 1573 |
| parquet_partitioned | movement | 71 | 4093976 | 13680765 | 1.076985 | 1194 |
| parquet_partitioned | period | 1 | 61880 | 13680765 | 0.706005 | 7720 |

## Join strategy

| Strategy | Median seconds | Output rows |
|---|---:|---:|
| standard | 0.43486 | 14181 |
| broadcast | 0.319699 | 14181 |

Every storage format and both join strategies produced identical row counts and checksums.
Shuffle bytes remain unavailable because PySpark 3.5 has no stable public per-query Python metrics API. HDFS is not configured on this machine.

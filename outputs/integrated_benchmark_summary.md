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
| csv | artist | 1 | 134485879 | 134485879 | 2.263888 | 1573 |
| csv | movement | 1 | 134485879 | 134485879 | 2.09054 | 1194 |
| csv | period | 1 | 134485879 | 134485879 | 2.129056 | 7725 |
| json | artist | 1 | 332336831 | 332336831 | 2.259816 | 1573 |
| json | movement | 1 | 332336831 | 332336831 | 2.144156 | 1194 |
| json | period | 1 | 332336831 | 332336831 | 2.383648 | 7725 |
| parquet_unpartitioned | artist | 1 | 705115 | 705115 | 0.807722 | 1573 |
| parquet_unpartitioned | movement | 1 | 705115 | 705115 | 0.76179 | 1194 |
| parquet_unpartitioned | period | 1 | 705115 | 705115 | 0.695844 | 7725 |
| parquet_partitioned | artist | 220 | 13695687 | 13695687 | 1.434266 | 1573 |
| parquet_partitioned | movement | 71 | 4104852 | 13695687 | 0.952041 | 1194 |
| parquet_partitioned | period | 1 | 61913 | 13695687 | 0.657919 | 7725 |

## Join strategy

| Strategy | Median seconds | Output rows |
|---|---:|---:|
| standard | 0.442307 | 14181 |
| broadcast | 0.345237 | 14181 |

Every storage format and both join strategies produced identical row counts and checksums.
Shuffle bytes remain unavailable because PySpark 3.5 has no stable public per-query Python metrics API. HDFS is not configured on this machine.

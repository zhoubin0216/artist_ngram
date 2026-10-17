# Stage B: Ngram clusters compared with OpenAlex

The Ngram Stage A3 cluster labels are fixed. No OpenAlex training was performed.

- A3 artists: **575**
- Any OpenAlex work in the aligned 80-year window: **166**
- Adequate signal (>= 3 works, >= 2 distinct ten-year bins): **91**
- Manually flagged identity risks from A3: **34**

## Cluster-level summary

| A3 cluster | Ngram artists | OA any-work artists | OA signal-qualified artists | Median Ngram–OA Spearman rho (qualified only) |
|---|---:|---:|---:|---:|
| 0 | 391 | 136 | 79 | 0.491 |
| 1 | 184 | 30 | 12 | 0.355 |

## Interpretation safeguards

The OA trend charts are observational corroboration checks, not external ground-truth labels.
Do not describe differences as causal or as proof of artist identity.
The OA window contains different calendar years for artists dying in different years.
Review `death_cohort_comparison.csv` before interpreting group effects.
The 80-year comparison window is the same for both sources and no outside-window years were treated as zero.
Artists with insufficient OpenAlex signal are retained in the full cohort coverage tables.

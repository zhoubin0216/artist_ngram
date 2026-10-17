# Stage C — Getty validation of fixed Ngram A3 clusters

## Scope and denominator

- A3 artists: **575**; cluster sizes: 0: 391, 1: 184.
- Accepted Getty mappings in A3: **291**.
- Artists with dated Getty events inside death -30:+49: **114**.
- Getty full dataset events without usable year: **56708/79715**.

## Coverage by cluster (denominator: ALL A3 artists)

| Ngram cluster | Getty matched | Dated event in window | Confirmed sale | Catalog entry |
|---|---:|---:|---:|---:|
| 0 | 187/391 (47.8%) | 71/391 (18.2%) | 71/391 (18.2%) | 69/391 (17.6%) |
| 1 | 104/184 (56.5%) | 43/184 (23.4%) | 43/184 (23.4%) | 43/184 (23.4%) |

## Three-source observation overlap

- Both OpenAlex and Getty have at least one in-window indexed record: **53** artists.
- Meet BOTH OpenAlex and Getty trend thresholds: **27** artists.
- This is a *coverage* check, not evidence that the two sources agree in shape.

## Interpretation guidance

1. Compare **coverage first**, not cluster-averaged event counts alone.
2. Use `getty_event_prevalence_relative.png` for bin-level fraction with any recorded event, conditional on accepted Getty mappings.
3. The event-count chart is outlier-sensitive; do not call it a market-wide transaction trend.
4. Dated event coverage and identity-matching quality differ between historical periods and clusters. Cohort table is descriptive only.
5. Use `coverage_without_name_warnings.csv` for an A2-name-risk check; it does not resolve Getty identity errors.
6. `getty_case_candidates.csv` selects event-rich unflagged artists; it is NOT a random or centroid-representative sample.
7. Do not fuse raw Getty counts, normalized Ngram rates, and OpenAlex article rates into one impact number.

## Figures

- `figures/getty_coverage_by_cluster.png`
- `figures/getty_event_prevalence_relative.png`
- `figures/getty_event_counts_relative.png`
- `figures/three_source_coverage.png`

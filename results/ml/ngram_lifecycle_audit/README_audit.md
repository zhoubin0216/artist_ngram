# Stage A2 — Ngram lifecycle and name-quality audit

- Included cluster assignments: **876**; group sizes: **{'0': 203, '1': 673}**.
- Birth year available: 876; death year available: 876.
- Artists with any pre-birth exact-name hits: **170**.
- Artists with pre-birth hit share > 1%: **47** (review flag, not identity rejection).
- Artists with annual normalized peak before birth: **22** (review flag).
- 25-year birth-cohort association (Cramer's V): **0.4234371148910512**.

## Interpretation

Cramer's V is descriptive association, not proof of a historical mechanism. Earlier and later-born artists may differ in observation windows. The original K-Means baseline was not modified. Review flagged high-risk names without automatically excluding them.

## Outputs

- artist_lifecycle_audit.csv — one row per clustered artist, including birth/death, normalized annual peak, and warnings
- top_20_review_cases.csv — ordered by pre-birth match share
- birth_cohort_comparison.csv — cluster counts and within-cohort proportions
- figures/ — histograms and cohort-composition chart
- lifecycle_summary.json — validated aggregate counts

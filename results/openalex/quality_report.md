# OpenAlex cleaned dataset — quality report

## Scope

OpenAlex core; English metadata; 1800-2019; Visual Arts any topic; full canonical name in title or abstract; all document types

## Counts

- **collected_artists**: 1370
- **complete_artists**: 1370
- **artists_with_cleaned_hits**: 538
- **artists_with_zero_cleaned_hits**: 832
- **original_matched_records**: 15693
- **cleaned_matched_records**: 15679
- **human_decisions_applied**: 154
- **sparse_year_rows**: 5504
- **dense_year_rows**: 301400
- **possible_duplicate_title_groups**: 204
- **confirmed_wrong_entity_exclusions**: 10
- **precautionary_exclusions**: 4
- **total_excluded_from_analysis**: 14
- **excluded_pct_of_matches**: 0.0892
- **reviewed sample labels**: {'relevant': 141, 'irrelevant': 8, 'uncertain': 1}
- **cleaning statuses**: {'include': 15679, 'exclude': 14}
- **decision reasons**: {'not_manually_reviewed': 15539, 'human_relevant': 140, 'human_wrong_entity': 10, 'conservative_entity_uncertain': 1, 'conservative_metadata_suspicious_year': 1, 'conservative_metadata_language_mismatch': 2}

## Interpretation

`include` means **accepted by the recorded cleaning rules**, not 100% manually verified. 14 human-identified problem records are marked `exclude` from final analysis: 10 confirmed wrong-entity matches and 4 precautionary exclusions for uncertain identity or unreliable publication metadata; the latter do NOT imply false identity. Original input is unchanged; see `excluded_from_analysis.csv` for every adjustment.

## Known limitations

- This is NOT a fully entity-disambiguated dataset: most works have not been manually evaluated.
- OpenAlex English language labels can be imperfect; suspicious manually identified records are excluded conservatively.
- OpenAlex subject classification and historical coverage vary by year.
- Unseen papers with aliases/surnames are not in scope: only canonical full English name is queried.
- The unweighted manual sample relevant ratio is not population precision: stratified sampling.
- Cross-source trend comparison should account for temporal coverage and possible normalization.

## Reproducibility

`cleaning_summary.json` records SHA-256 hashes of the two decision CSVs and the original summary/status files. The raw JSONL source remains unmodified.

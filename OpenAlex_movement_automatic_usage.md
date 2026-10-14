# Automatic movement context classification

Replace `scripts/openalex_movements.py` with the supplied version. Add `movement_auto_rules.py` beside it. Replace `tests/test_openalex_movements.py` with the supplied tests. Keep the existing collector, collection, term CSV and human review CSV unchanged.

This version categorizes every direct movement-work record using conservative title/abstract context rules. Labels are automated suggestions, not manual verification. Rule strength is an evidence category, not a calibrated probability.

## Tests

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_openalex_movements.py" -v
```

## Regenerate classifications without API access

```powershell
.\.venv\Scripts\python.exe scripts/openalex_movements.py classify --reviews config/openalex_movement_review.csv --output results/openalex_movement_classification
```

All current direct matches are classified. Existing manual labels are preserved separately. Generated files:

- `movement_automatic_classification.csv`: all records, with `automatic_label`, `automatic_reason`, `rule_strength` and evidence flags.
- `automatic_relevant.csv`: records supported by the context rules.
- `automatic_irrelevant.csv`: explicit wrong-sense matches detected by the rules.
- `automatic_uncertain.csv`: records for which the available rules cannot establish painting-movement relevance.
- `automatic_classification_summary.json`: totals, rules version and provenance.

The input review CSV is not overwritten. Existing movement raw/candidate archives and artist data are not modified.

## Rebuild a broad provisional union

```powershell
.\.venv\Scripts\python.exe scripts/openalex_movements.py build --reviews results/openalex_movement_classification/movement_automatic_classification.csv --use-automatic-labels --auto-uncertain retain --allow-unreviewed --output results/openalex_movements_auto_broad
```

This retains automatic relevant and uncertain matches, excluding automatic irrelevant matches. Human decisions override machine labels. Uncertain matches remain unreviewed in the report and are not relabeled relevant.

## Rebuild a conservative union

```powershell
.\.venv\Scripts\python.exe scripts/openalex_movements.py build --reviews results/openalex_movement_classification/movement_automatic_classification.csv --use-automatic-labels --auto-uncertain exclude --output results/openalex_movements_auto_strict
```

This excludes automatic uncertain as well as automatic irrelevant direct matches. The first exclusion is precautionary: it does not prove the work is irrelevant. Use the broad and strict results as a sensitivity comparison, not as bounds with statistical coverage guarantees.

Both builds preserve artist-linked evidence independently. Rejecting a direct movement-word match does not erase an already accepted artist-linked work. The audit file records `direct_decision_source`, `direct_decision_label` and `automatic_label`. Report summaries distinguish automatic, human and unreviewed decisions.

The build recalculates automatic labels from source records and the current rules; edited machine labels are rejected. If you override a classification after actual review, fill `manual_label` and keep the machine columns for provenance.

## Rule scope

The rules look for movement names, visual-art vocabulary and explicit non-art senses. Generic Symbolism, related Realism schools, media-specific contexts and parent-movement substring matches remain uncertain unless supported. They cannot resolve all multilingual text, subtle meanings or incidental mentions.

The supplied full classification covers all source records; it does not mean a human has read every record. Do not claim fully manually verified labels, measured precision or primary-discussion status. No labeled validation set is available to estimate classifier accuracy.

The generated broad and strict annual series measure different operational scopes. Keep that distinction in the analysis and report.

#!/usr/bin/env python3
"""Reproducible, NON-DESTRUCTIVE cleaning of artist-focused OpenAlex exports.

Input: collect_openalex.py's data/openalex_art/core/any/full directory.
Human decisions: CSVs converted from OpenAlex_full_150_manual_review_final.xlsx.
No network calls, no Wikidata queries, no changes to the original collector or raw files.

Semantics:
 - INCLUDE: unreviewed or human-relevant, with no outstanding human metadata issue.
 - EXCLUDE: confirmed wrong entity OR conservative exclusion of uncertain identity/metadata.
   Reasons and evidence strength remain separate; exclusion does not imply wrong identity.
 - Collector's risk_flags are *warnings only*, never grounds to exclude.

The output is a cleaned FULL-NAME MENTION PROXY; it is not a fully entity-resolved set.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
START_YEAR, END_YEAR = 1800, 2019
ENTITY_LABELS = {"relevant", "irrelevant", "uncertain"}
METADATA_ISSUES = {"none", "suspicious_year", "language_mismatch", "possible_duplicate", "other"}
HELD_METADATA = {"suspicious_year", "language_mismatch", "possible_duplicate", "other"}
DETAIL_FIELDS = ["artist_id", "artist_name", "work_id", "publication_year", "type", "title",
                 "entity_label", "metadata_issue", "cleaning_status", "cleaning_reason", "exclusion_basis", "review_source", "review_notes"]
YEAR_FIELDS = ["artist_id", "artist_name", "year", "is_complete", "art_works_count", "works_count",
               "primary_visual_arts_count", "flagged_works_count", "with_abstract_count", "title_match_count"]
TYPE_FIELDS = ["artist_id", "year", "type", "works_count"]


def read_csv(path: Path, required: set[str]) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing CSV columns in {path}: {sorted(missing)}")
        return list(reader)


def write_csv(path: Path, fields: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def work_key(artist_id: str, work_id: str) -> tuple[str, str]:
    artist_id = (artist_id or "").strip()
    match = re.search(r"(?:^|/)W(\d+)$", (work_id or "").strip(), re.I)
    if not artist_id or not match:
        raise ValueError(f"Bad artist_id/work_id pair: {artist_id!r}, {work_id!r}")
    return artist_id, "W" + match.group(1)


def clean_issue(value: str | None) -> str:
    issue = (value or "none").strip().lower() or "none"
    if issue not in METADATA_ISSUES:
        raise ValueError(f"Unknown metadata_issue: {issue!r}")
    return issue


def load_decisions(review_csv: Path, issue_csv: Path) -> tuple[dict, dict]:
    """Combine independent human sources; detect conflicts; known issues can add metadata flags."""
    reviews = read_csv(review_csv, {"artist_id", "work_id", "manual_label", "metadata_issue"})
    known = read_csv(issue_csv, {"artist_id", "work_id", "entity_label", "metadata_issue"})
    decisions = {}
    for i, row in enumerate(reviews, start=2):
        key = work_key(row["artist_id"], row["work_id"])
        label = row["manual_label"].strip().lower()
        if label not in ENTITY_LABELS:
            raise ValueError(f"Invalid manual_label at {review_csv}:{i}: {label!r}")
        if key in decisions:
            raise ValueError(f"Duplicate reviewed artist-work key at {review_csv}:{i}: {key}")
        decisions[key] = {"entity_label": label, "metadata_issue": clean_issue(row.get("metadata_issue")),
                          "review_source": "manual_sample", "review_notes": row.get("review_notes", "").strip()}
    for i, row in enumerate(known, start=2):
        key = work_key(row["artist_id"], row["work_id"])
        label = row["entity_label"].strip().lower()
        if label not in ENTITY_LABELS:
            raise ValueError(f"Invalid entity_label at {issue_csv}:{i}: {label!r}")
        issue = clean_issue(row.get("metadata_issue"))
        reason = (row.get("reason") or "").strip()
        if key in decisions:
            old = decisions[key]
            if label != old["entity_label"] or (issue != "none" and
                                                  old["metadata_issue"] not in {"none", issue}):
                raise ValueError(f"Conflicting human decisions for {key} between review and known issues")
            old["review_source"] += "+known_issue"
            if issue != "none":
                old["metadata_issue"] = issue
            if reason:
                old["review_notes"] = (old["review_notes"] + "; " + reason).strip("; ")
        else:
            decisions[key] = {"entity_label": label, "metadata_issue": issue,
                              "review_source": "known_issue", "review_notes": reason}
    return decisions, {"sample_rows": len(reviews), "known_issue_rows": len(known),
                       "sample_label_counts": dict(Counter(d["manual_label"].strip().lower() for d in reviews))}


def decision_for(row: dict, human: dict | None) -> dict:
    """Entity identity and metadata validity are orthogonal."""
    label = human["entity_label"] if human else "unreviewed"
    issue = human["metadata_issue"] if human else "none"
    if label == "irrelevant":
        status, reason, basis = "exclude", "human_wrong_entity", "confirmed_wrong_entity"
    elif label == "uncertain":
        status, reason, basis = "exclude", "conservative_entity_uncertain", "precautionary"
    elif issue in HELD_METADATA:
        status, reason, basis = "exclude", f"conservative_metadata_{issue}", "precautionary"
    else:
        status, reason, basis = "include", "human_relevant" if human else "not_manually_reviewed", "none"
    return {"entity_label": label, "metadata_issue": issue, "cleaning_status": status,
            "cleaning_reason": reason, "exclusion_basis": basis,
            "review_source": human["review_source"] if human else "none",
            "review_notes": human["review_notes"] if human else ""}


def normalized_title(title: str) -> str:
    title = unicodedata.normalize("NFKD", title or "")
    title = "".join(c for c in title if not unicodedata.combining(c)).casefold()
    return " ".join(re.findall(r"[^\W_]+", title))


def ensure_raw_matches_summaries(input_dir: Path, raw_year: Counter, raw_type: Counter) -> None:
    source_y = read_csv(input_dir / "artist_year.csv", {"artist_id", "year", "art_works_count"})
    source_t = read_csv(input_dir / "artist_year_type.csv", {"artist_id", "year", "type", "works_count"})
    check_y = Counter()
    check_t = Counter()
    for r in source_y:
        key = (r["artist_id"], int(r["year"]))
        if key in check_y:
            raise ValueError(f"Duplicate artist-year row in source summary: {key}")
        check_y[key] = int(r["art_works_count"])
    for r in source_t:
        key = (r["artist_id"], int(r["year"]), r["type"])
        if key in check_t:
            raise ValueError(f"Duplicate artist-year-type row in source summary: {key}")
        check_t[key] = int(r["works_count"])
    if raw_year != check_y:
        raise ValueError(f"Raw JSONL disagrees with original artist_year.csv (raw={sum(raw_year.values())}, "
                         f"summary={sum(check_y.values())}). Do not clean until investigated.")
    if raw_type != check_t:
        raise ValueError("Raw JSONL disagrees with original artist_year_type.csv")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def run(input_dir: Path, review_csv: Path, known_issues_csv: Path, output_dir: Path,
        expected_artists: int = 1370) -> dict:
    if output_dir.resolve() == input_dir.resolve() or input_dir.resolve() in output_dir.resolve().parents:
        raise ValueError("Output directory must be outside the input/raw directory")
    statuses = read_csv(input_dir / "artist_collection_status.csv", {"artist_id", "artist_name", "status"})
    artists = {}
    for item in statuses:
        aid = item['artist_id'].strip()
        if aid in artists:
            raise ValueError(f"Duplicate artist in collection status: {aid}")
        artists[aid] = item
    if len(artists) != expected_artists:
        raise ValueError(f"Expected {expected_artists} artists, found {len(artists)}")
    incomplete = [aid for aid, row in artists.items() if row['status'].strip().lower() != 'complete']
    if incomplete:
        raise ValueError(f"Source collection incomplete: {len(incomplete)} artists, e.g. {incomplete[:5]}")
    for aid, row in artists.items():
        if (row.get('topic_scope') or '').strip() not in ('any', ''):
            raise ValueError(f"Input scope is not Visual Arts ANY topic for {aid}")
        if (row.get('art_subfield_id') or '').strip() not in ('1213', ''):
            raise ValueError(f"Input subfield is not Visual Arts and Performing Arts for {aid}")
        if (row.get('corpus') or '').strip() not in ('core', ''):
            raise ValueError(f"Input corpus is not OpenAlex core for {aid}")
    decisions, decision_stats = load_decisions(review_csv, known_issues_csv)
    raw_dir = input_dir / 'raw'
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"Missing raw JSONL folder: {raw_dir}")
    raw_year = Counter()
    raw_type = Counter()
    year_groups = defaultdict(list)
    clean_type = Counter()
    dispositions = Counter()
    reasons = Counter()
    risk_flags = Counter()
    original_count = 0
    seen = set()
    found_reviews = set()
    reviewed_results = []
    changed_rows = []
    included_rows = []
    title_groups = defaultdict(list)

    for aid in sorted(artists):
        file = raw_dir / f'{aid}.jsonl'
        if not file.is_file():
            raise FileNotFoundError(f"Collection complete but raw file missing: {file}")
        with file.open('r', encoding='utf-8') as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON at {file}:{line_no}") from e
                key = work_key(row.get('artist_id'), row.get('work_id'))
                if key[0] != aid:
                    raise ValueError(f"Artist ID disagrees with raw filename: {file}:{line_no}")
                if key in seen:
                    raise ValueError(f"Duplicate work ID within same artist: {key}")
                seen.add(key)
                year = row.get('publication_year')
                if not isinstance(year, int) or not START_YEAR <= year <= END_YEAR:
                    raise ValueError(f"Out-of-range or missing publication_year: {file}:{line_no}")
                if row.get('language') != 'en' or not row.get('is_visual_arts', False):
                    raise ValueError(f"Input no longer meets language/art topic filters: {file}:{line_no}")
                if not (row.get('match_in_title') or row.get('match_in_abstract')):
                    raise ValueError(f"Input name not verified in title/abstract: {file}:{line_no}")
                typ = row.get('type') or 'unknown'
                original_count += 1
                raw_year[(aid, year)] += 1
                raw_type[(aid, year, typ)] += 1
                for flag in (row.get('risk_flags') or []):
                    risk_flags[flag] += 1
                human = decisions.get(key)
                if human:
                    found_reviews.add(key)
                mark = decision_for(row, human)
                dispositions[mark['cleaning_status']] += 1
                reasons[mark['cleaning_reason']] += 1
                detail = {**row, **mark}
                if human:
                    reviewed_results.append({field: detail.get(field, '') for field in DETAIL_FIELDS})
                if mark['cleaning_status'] != 'include':
                    changed_rows.append({field: detail.get(field, '') for field in DETAIL_FIELDS})
                else:
                    included_rows.append(detail)
                    year_groups[(aid, year)].append(detail)
                    clean_type[(aid, year, typ)] += 1
                    title_key = normalized_title(str(row.get('title') or ''))
                    if len(title_key) >= 12:
                        title_groups[(aid, year, title_key)].append(detail)
    unknown = set(decisions) - found_reviews
    if unknown:
        raise ValueError(f"Human decisions refer to {len(unknown)} artist/work pairs absent from raw data, "
                         f"e.g. {sorted(unknown)[:5]}")
    ensure_raw_matches_summaries(input_dir, raw_year, raw_type)

    output_dir.mkdir(parents=True, exist_ok=True)
    # Full retained detail is JSONL so title/abstract, original fields and review provenance remain traceable.
    with (output_dir / 'artist_work_clean.jsonl').open('w', encoding='utf-8') as f:
        for row in included_rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    write_csv(output_dir / 'manual_decisions_applied.csv', DETAIL_FIELDS, reviewed_results)
    write_csv(output_dir / 'excluded_from_analysis.csv', DETAIL_FIELDS, changed_rows)
    # Backward-compatible alias for users of v1 report; no records are 'hold' under final policy.
    write_csv(output_dir / 'held_and_excluded.csv', DETAIL_FIELDS, changed_rows)
    year_rows = []
    for (aid, year), works in sorted(year_groups.items()):
        count = len(works)
        year_rows.append({"artist_id": aid, "artist_name": artists[aid]['artist_name'], "year": year,
                          "is_complete": 1, "art_works_count": count, "works_count": count,
                          "primary_visual_arts_count": sum(bool(r.get('primary_is_visual_arts')) for r in works),
                          "flagged_works_count": sum(bool(r.get('risk_flags')) for r in works),
                          "with_abstract_count": sum(bool(r.get('has_abstract')) for r in works),
                          "title_match_count": sum(bool(r.get('match_in_title')) for r in works)})
    write_csv(output_dir / 'artist_year_clean.csv', YEAR_FIELDS, year_rows)
    write_csv(output_dir / 'artist_year_type_clean.csv', TYPE_FIELDS,
              ({"artist_id": a, "year": y, "type": t, "works_count": n}
               for (a, y, t), n in sorted(clean_type.items())))
    counts_by_artist = Counter({aid: 0 for aid in artists})
    counts_by_artist.update({aid: sum(len(rows) for (artist, _), rows in year_groups.items() if artist == aid)
                             for aid in artists})
    # Complete 1800–2019 grid is convenient for integration; zeros do not mean missing API queries.
    lookup = {(row['artist_id'], row['year']): row for row in year_rows}
    with (output_dir / 'artist_year_dense_clean.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['artist_id', 'artist_name', 'year', 'works_count', 'is_complete'])
        writer.writeheader()
        for aid in sorted(artists):
            for year in range(START_YEAR, END_YEAR + 1):
                value = lookup.get((aid, year))
                writer.writerow({'artist_id': aid, 'artist_name': artists[aid]['artist_name'], 'year': year,
                                 'works_count': value['works_count'] if value else 0, 'is_complete': 1})
    report_per_artist = []
    original_by_artist = Counter()
    for (aid, _), n in raw_year.items():
        original_by_artist[aid] += n
    exc_by_artist = Counter(r['artist_id'] for r in changed_rows if r['cleaning_status'] == 'exclude')
    hold_by_artist = Counter(r['artist_id'] for r in changed_rows if r['cleaning_status'] == 'hold')
    for aid in sorted(artists):
        report_per_artist.append({'artist_id': aid, 'artist_name': artists[aid]['artist_name'],
                                  'raw_mentions': original_by_artist[aid], 'clean_mentions': counts_by_artist[aid],
                                  'excluded_mentions': exc_by_artist[aid], 'held_mentions': hold_by_artist[aid],
                                  'collection_complete': 1})
    write_csv(output_dir / 'artist_cleaning_status.csv',
              ['artist_id','artist_name','raw_mentions','clean_mentions','excluded_mentions','held_mentions','collection_complete'],
              report_per_artist)
    shutil.copyfile(input_dir / 'artist_collection_status.csv', output_dir / 'artist_collection_status.csv')

    duplicates = []
    for (aid, year, title), matches in sorted(title_groups.items()):
        if len(matches) > 1:
            duplicates.append({'artist_id': aid, 'year': year, 'normalized_title': title,
                               'work_ids': '|'.join(r['work_id'] for r in matches), 'records': len(matches),
                               'note': 'Review only: same title/year can correspond to legitimate distinct editions'})
    write_csv(output_dir / 'possible_duplicate_titles.csv',
              ['artist_id', 'year', 'normalized_title', 'work_ids', 'records', 'note'], duplicates)
    clean_total = len(included_rows)
    assert sum(r['art_works_count'] for r in year_rows) == clean_total
    assert sum(clean_type.values()) == clean_total
    assert original_count == sum(dispositions.values())
    assert original_count == clean_total + len(changed_rows)
    report = {
        'definition': 'OpenAlex core; English metadata; 1800-2019; Visual Arts any topic; full canonical name in title or abstract; all document types',
        'cleaning_policy': {
            'exclude': 'confirmed wrong-entity records plus precautionary exclusions for manually uncertain identity/metadata',
            'hold': 'not used in final policy; previously held records are excluded from analysis',
            'include': 'remaining records, including UNREVIEWED matches',
            'automated_flags': 'recorded but NEVER automatically excluded',
            'duplicate_policy': 'one artist-work ID; cross-artist same Work IDs allowed; same title/year only flagged',
        },
        'input_dir': str(input_dir), 'review_csv': str(review_csv), 'known_issues_csv': str(known_issues_csv),
        'input_hashes_sha256': {'review_csv': sha256(review_csv), 'known_issues_csv': sha256(known_issues_csv),
                                'collection_status_csv': sha256(input_dir / 'artist_collection_status.csv'),
                                'original_artist_year_csv': sha256(input_dir / 'artist_year.csv')},
        'collected_artists': len(artists), 'complete_artists': len(artists),
        'artists_with_cleaned_hits': sum(v > 0 for v in counts_by_artist.values()),
        'artists_with_zero_cleaned_hits': sum(v == 0 for v in counts_by_artist.values()),
        'original_matched_records': original_count, 'cleaned_matched_records': clean_total,
        'statuses': dict(dispositions), 'decision_reasons': dict(reasons),
        'confirmed_wrong_entity_exclusions': sum(r['exclusion_basis'] == 'confirmed_wrong_entity' for r in changed_rows),
        'precautionary_exclusions': sum(r['exclusion_basis'] == 'precautionary' for r in changed_rows),
        'total_excluded_from_analysis': len(changed_rows),
        'excluded_pct_of_matches': round(100 * len(changed_rows) / original_count, 4) if original_count else 0,
        'raw_flag_counts': dict(risk_flags),
        'sample_review_labels': decision_stats['sample_label_counts'],
        'sample_reviewed': decision_stats['sample_rows'], 'known_issues_reviewed': decision_stats['known_issue_rows'],
        'human_decisions_applied': len(found_reviews), 'human_actions_excluded_or_held': len(changed_rows),
        'sparse_year_rows': len(year_rows), 'dense_year_rows': len(artists) * (END_YEAR-START_YEAR+1),
        'possible_duplicate_title_groups': len(duplicates),
        'important_caveats': [
            'This is NOT a fully entity-disambiguated dataset: most works have not been manually evaluated.',
            'OpenAlex English language labels can be imperfect; suspicious manually identified records are excluded conservatively.',
            'OpenAlex subject classification and historical coverage vary by year.',
            'Unseen papers with aliases/surnames are not in scope: only canonical full English name is queried.',
            'The unweighted manual sample relevant ratio is not population precision: stratified sampling.',
            'Cross-source trend comparison should account for temporal coverage and possible normalization.'
        ]
    }
    (output_dir / 'cleaning_summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (output_dir / 'quality_report.md').open('w', encoding='utf-8') as f:
        f.write('# OpenAlex cleaned dataset — quality report\n\n')
        f.write('## Scope\n\n' + report['definition'] + '\n\n')
        f.write('## Counts\n\n')
        for k in ('collected_artists','complete_artists','artists_with_cleaned_hits',
                  'artists_with_zero_cleaned_hits','original_matched_records','cleaned_matched_records',
                  'human_decisions_applied','sparse_year_rows','dense_year_rows','possible_duplicate_title_groups',
                  'confirmed_wrong_entity_exclusions','precautionary_exclusions','total_excluded_from_analysis',
                  'excluded_pct_of_matches'):
            f.write(f'- **{k}**: {report[k]}\n')
        f.write(f'- **reviewed sample labels**: {report["sample_review_labels"]}\n')
        f.write(f'- **cleaning statuses**: {report["statuses"]}\n')
        f.write(f'- **decision reasons**: {report["decision_reasons"]}\n\n')
        f.write('## Interpretation\n\n')
        f.write('`include` means **accepted by the recorded cleaning rules**, not 100% manually verified. '
                f'{report["total_excluded_from_analysis"]} human-identified problem records are marked `exclude` from final analysis: '
                f'{report["confirmed_wrong_entity_exclusions"]} confirmed wrong-entity matches and '
                f'{report["precautionary_exclusions"]} precautionary exclusions for uncertain identity '
                'or unreliable publication metadata; the latter do NOT imply false identity. '
                'Original input is unchanged; see `excluded_from_analysis.csv` for every adjustment.\n\n')
        f.write('## Known limitations\n\n')
        for line in report['important_caveats']:
            f.write(f'- {line}\n')
        f.write('\n## Reproducibility\n\n`cleaning_summary.json` records SHA-256 hashes of the two decision CSVs '
                'and the original summary/status files. The raw JSONL source remains unmodified.\n')
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('--input-dir', type=Path, default=ROOT / 'data/openalex_art/core/any/full',
                    help='Directory containing raw/, artist_year.csv and artist_collection_status.csv')
    ap.add_argument('--reviews', type=Path, default=ROOT / 'config/openalex_manual_review_150.csv')
    ap.add_argument('--known-issues', type=Path, default=ROOT / 'config/openalex_known_issues.csv')
    ap.add_argument('--output-dir', type=Path, default=ROOT / 'results/openalex')
    ap.add_argument('--expected-artists', type=int, default=1370)
    args = ap.parse_args()
    try:
        stats = run(args.input_dir, args.reviews, args.known_issues, args.output_dir, args.expected_artists)
    except (OSError, ValueError, AssertionError) as e:
        print(f'[CLEANING FAILED] {e}', file=sys.stderr)
        raise SystemExit(1) from e
    print(json.dumps({k: stats[k] for k in (
        'collected_artists','original_matched_records','cleaned_matched_records','statuses',
        'human_decisions_applied','artists_with_zero_cleaned_hits','sparse_year_rows','dense_year_rows')},
        ensure_ascii=False, indent=2))
    print(f'Output written to: {args.output_dir.resolve()}')


if __name__ == '__main__':
    main()

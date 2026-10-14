"""Movement-name collection and artist-linked Work-ID union.
Place beside collect_openalex.py. Run from the repository root.
Existing artist source and cleaned files are read-only.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import requests
import collect_openalex as collector
import movement_auto_rules as auto_rules

START, END = 1800, 2019
ROOT = Path(__file__).resolve().parents[1]
SCOPE = {'corpus': 'core', 'topic_scope': 'any', 'subfield': '1213',
         'language': 'en', 'start_year': START, 'end_year': END, 'types': 'all'}
FILTER = 'from_publication_date:1800-01-01,to_publication_date:2019-12-31,language:en,topics.subfield.id:1213'

def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def save_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

def json_rows(path):
    with path.open(encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try: yield json.loads(line)
                except ValueError as exc: raise ValueError(f'Invalid JSON: {path}:{n}') from exc

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)

def work_id(value):
    match = re.fullmatch(r'(?:https?://openalex\.org/)?(W\d+)', value or '', re.I)
    if not match: raise ValueError(f'Invalid Work ID: {value}')
    return match.group(1).upper()

def dictionary(path):
    rows = csv_rows(path); artists = {}; movements = {}
    for row in rows:
        aid = row['artist_id']
        if aid in artists: raise ValueError('Duplicate artist: ' + aid)
        ids = [v.strip() for v in row['movement_ids'].split(';') if v.strip()]
        names = [v.strip() for v in row['movement_names'].split(';') if v.strip()]
        if not ids or len(ids) != len(names) or len(ids) != len(set(ids)):
            raise ValueError('Invalid movement mapping: ' + aid)
        artists[aid] = ids
        for mid, name in zip(ids, names):
            if mid in movements and movements[mid] != name:
                raise ValueError('Conflicting movement name: ' + mid)
            movements[mid] = name
    if not artists: raise ValueError('Empty dictionary')
    return artists, movements

def terms(path, movements):
    rows = csv_rows(path); result = []; seen = set()
    for row in rows:
        mid, term = row['movement_id'].strip(), row['search_term'].strip()
        if mid not in movements or row['movement_name'] != movements[mid] or not term or '"' in term:
            raise ValueError('Invalid movement term: ' + str(row))
        key = (mid, collector.normalize(term))
        if key in seen: raise ValueError('Duplicate term: ' + str(key))
        seen.add(key)
        result.append({'movement_id': mid, 'movement_name': movements[mid], 'search_term': term})
    if {r['movement_id'] for r in result} != set(movements):
        raise ValueError('Every dictionary movement needs at least one search term')
    return result

def term_key(row):
    return row['movement_id'] + '_' + hashlib.sha256(row['search_term'].encode()).hexdigest()[:16]

def init(args):
    _, movements = dictionary(args.dictionary)
    if args.terms.exists(): raise ValueError('Terms file exists; edit it rather than overwrite it')
    save_csv(args.terms, ['movement_id', 'movement_name', 'search_term'],
             ({'movement_id': mid, 'movement_name': name, 'search_term': name}
              for mid, name in sorted(movements.items())))
    print('Created canonical movement terms:', args.terms)

def collect(args):
    _, movements = dictionary(args.dictionary); query_terms = terms(args.terms, movements)
    key = os.getenv('OPENALEX_API_KEY', '').strip()
    if not key: raise ValueError('Set OPENALEX_API_KEY')
    folder = args.collection
    for name in ['raw', 'candidates']: (folder / name).mkdir(parents=True, exist_ok=True)
    checkpoint = folder / 'collection_status.json'
    status = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {}
    with requests.Session() as session:
        for query in query_terms:
            qkey = term_key(query); raw = folder / 'raw' / (qkey + '.jsonl')
            candidate = folder / 'candidates' / raw.name
            previous = status.get(qkey, {})
            if (previous.get('status') == 'complete' and previous.get('query') == query
                and previous.get('scope') == SCOPE and raw.exists() and candidate.exists()
                and previous.get('raw_sha256') == sha(raw)
                and previous.get('candidate_sha256') == sha(candidate)):
                print('Cached complete:', query['movement_name'], query['search_term']); continue
            rt = raw.with_suffix('.tmp'); ct = candidate.with_suffix('.tmp')
            cursor = '*'; cursors = set(); seen = set(); candidate_count = rejected = pages = 0
            first_total = None
            try:
                with rt.open('w', encoding='utf-8') as rf, ct.open('w', encoding='utf-8') as cf:
                    while cursor:
                        if cursor in cursors: raise ValueError('Repeated cursor: ' + qkey)
                        cursors.add(cursor)
                        params = {'search.title_and_abstract': '"' + query['search_term'] + '"',
                                  'filter': FILTER, 'corpus': 'core', 'per_page': 100,
                                  'cursor': cursor, 'select': collector.SELECT}
                        data = collector.request_page(session, params, key, args.delay)
                        meta = data.get('meta', {})
                        if 'results' not in data or 'count' not in meta or 'next_cursor' not in meta:
                            raise ValueError('Unexpected API page')
                        if first_total is None: first_total = int(meta['count'])
                        if int(meta['count']) != first_total: raise ValueError('API total changed during pagination; retry later')
                        pages += 1
                        for work in data['results']:
                            # Reuse the exact local name/topic/language/year verification.
                            row = collector.make_row(work, {'artist_id': query['movement_id'],
                                                      'canonical_name': query['search_term']}, 'any')
                            cf.write(json.dumps({**query, 'locally_verified': row is not None,
                                                 'work': work}, ensure_ascii=False) + '\n')
                            candidate_count += 1
                            if row is None: rejected += 1; continue
                            wid = work_id(row['work_id'])
                            if wid in seen: continue
                            seen.add(wid)
                            row.pop('artist_id'); row.pop('artist_name')
                            rf.write(json.dumps({**row, **query, 'work_id': wid}, ensure_ascii=False) + '\n')
                        cursor = meta['next_cursor']
                        if cursor and not data['results']: raise ValueError('Empty page with next cursor')
                if candidate_count != first_total: raise ValueError('Candidate count differs from API total')
                os.replace(ct, candidate); os.replace(rt, raw)
            except Exception:
                rt.unlink(missing_ok=True); ct.unlink(missing_ok=True); raise
            status[qkey] = {'query': query, 'scope': SCOPE, 'status': 'complete', 'pages': pages,
                            'candidates': candidate_count, 'accepted': len(seen), 'rejected': rejected,
                            'raw_sha256': sha(raw), 'candidate_sha256': sha(candidate),
                            'fetched_at_utc': datetime.now(timezone.utc).isoformat()}
            save_json(checkpoint, status)
            print(query['movement_name'], query['search_term'], 'accepted:', len(seen))

def direct_rows(args, movements):
    query_terms = terms(args.terms, movements)
    status = json.loads((args.collection / 'collection_status.json').read_text(encoding='utf-8'))
    matches = {}
    for query in query_terms:
        qkey = term_key(query); st = status.get(qkey, {})
        raw = args.collection / 'raw' / (qkey + '.jsonl')
        candidate = args.collection / 'candidates' / raw.name
        if (st.get('status') != 'complete' or st.get('query') != query or st.get('scope') != SCOPE
            or not raw.exists() or not candidate.exists()
            or sha(raw) != st.get('raw_sha256') or sha(candidate) != st.get('candidate_sha256')):
            raise ValueError('Incomplete or changed collection: ' + qkey)
        rows = list(json_rows(raw))
        if len(rows) != st['accepted']: raise ValueError('Raw count mismatch: ' + qkey)
        seen = set()
        for row in rows:
            wid = work_id(row['work_id']); key = (query['movement_id'], wid)
            if wid in seen: raise ValueError('Duplicate raw Work ID')
            seen.add(wid)
            if any(row.get(f) != query[f] for f in query): raise ValueError('Raw query mismatch')
            if row['language'] != 'en' or not row['is_visual_arts'] or not START <= row['publication_year'] <= END:
                raise ValueError('Raw scope mismatch')
            if not (collector.contains_full_name(row['title'], query['search_term']) or
                    collector.contains_full_name(row.get('abstract', ''), query['search_term'])):
                raise ValueError('Unverified movement term')
            if key in matches and matches[key]['publication_year'] != row['publication_year']:
                raise ValueError('Different years for same Work ID')
            if key not in matches: matches[key] = {**row, 'matched_terms': set()}
            matches[key]['matched_terms'].add(query['search_term'])
    return matches

REVIEW_FIELDS = ['movement_id', 'movement_name', 'work_id', 'publication_year', 'title',
                 'abstract', 'matched_terms', 'manual_label', 'metadata_issue', 'review_notes']

def review(args):
    _, movements = dictionary(args.dictionary); matches = direct_rows(args, movements)
    if args.review_output.exists(): raise ValueError('Review file exists; never overwrite completed decisions')
    save_csv(args.review_output, REVIEW_FIELDS,
             ({**{f: r.get(f, '') for f in REVIEW_FIELDS}, 'matched_terms': '; '.join(sorted(r['matched_terms'])),
               'manual_label': '', 'metadata_issue': 'none'} for _, r in sorted(matches.items())))
    print('Exported all direct movement matches:', len(matches), 'to', args.review_output)

def classify(args):
    _, movements = dictionary(args.dictionary)
    matches = direct_rows(args, movements)
    old = load_reviews(args.reviews, matches) if args.reviews else {}
    rows = []
    for key, match in sorted(matches.items()):
        row = {f: match.get(f, '') for f in REVIEW_FIELDS}
        row['matched_terms'] = '; '.join(sorted(match['matched_terms']))
        previous = old.get(key, {'manual_label': '', 'metadata_issue': 'none', 'review_notes': ''})
        row.update({f: previous[f] for f in ['manual_label', 'metadata_issue', 'review_notes']})
        row.update(auto_rules.label(row))
        rows.append(row)
    fields = REVIEW_FIELDS + ['automatic_label', 'rule_strength', 'automatic_reason',
                              'classification_method', 'term_in_title',
                              'nearby_visual_art_evidence', 'nearby_other_media_evidence']
    args.output.mkdir(parents=True, exist_ok=True)
    save_csv(args.output / 'movement_automatic_classification.csv', fields, rows)
    for label in ['relevant', 'irrelevant', 'uncertain']:
        save_csv(args.output / ('automatic_' + label + '.csv'), fields,
                 [r for r in rows if r['automatic_label'] == label])
    summary = {'classification_method': auto_rules.VERSION, 'classification_is_human_review': False,
               'rule_strength_is_calibrated_probability': False, 'rows': len(rows),
               'label_counts': dict(Counter(r['automatic_label'] for r in rows)),
               'human_labels_preserved': sum(bool(r['manual_label']) for r in rows),
               'reason_counts': dict(Counter(r['automatic_reason'] for r in rows)),
               'per_movement': {mid: dict(Counter(r['automatic_label'] for r in rows if r['movement_id'] == mid))
                                for mid in sorted(movements)},
               'hashes': {'dictionary': sha(args.dictionary), 'terms': sha(args.terms),
                          'checkpoint': sha(args.collection / 'collection_status.json'),
                          'rules': sha(Path(auto_rules.__file__)),
                          'prior_reviews': sha(args.reviews) if args.reviews else None},
               'limitations': ['Rule-based context classification cannot establish true relevance for every record.',
                              'Automatic relevant means evidence under the rules, not guaranteed primary discussion.',
                              'Uncertain records must remain distinguishable from confirmed irrelevant records.']}
    save_json(args.output / 'automatic_classification_summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

def load_reviews(path, matches, use_automatic=False, auto_uncertain='retain'):

    decisions = {}
    if path is None: return decisions
    for r in csv_rows(path):
        key = (r['movement_id'].strip(), work_id(r['work_id'].strip()))
        label = r['manual_label'].strip().lower(); issue = r.get('metadata_issue', '').strip().lower() or 'none'
        if key in decisions or key not in matches: raise ValueError('Duplicate/unknown movement review: ' + str(key))
        if label not in {'', 'relevant', 'irrelevant', 'uncertain'}: raise ValueError('Invalid review label')
        if issue not in {'none', 'suspicious_year', 'language_mismatch', 'possible_duplicate', 'other'}:
            raise ValueError('Invalid metadata issue')
        if not label and issue != 'none': raise ValueError('Set a manual label for records with metadata issues')
        source = 'human' if label else 'unreviewed'
        auto_label = r.get('automatic_label', '')
        if use_automatic and not label:
            if r.get('classification_method') != auto_rules.VERSION or auto_label not in {'relevant', 'irrelevant', 'uncertain'}:
                raise ValueError('Missing or unsupported automatic classification')
            # Recompute evidence: do not trust edited machine labels.
            evidence = {**matches[key], 'matched_terms': '; '.join(sorted(matches[key]['matched_terms']))}
            calculated = auto_rules.label(evidence)
            if auto_label != calculated['automatic_label'] or r.get('automatic_reason') != calculated['automatic_reason']:
                raise ValueError('Automatic labels differ from current source/rules')
            label = auto_label if auto_label != 'uncertain' or auto_uncertain == 'exclude' else ''
            source = 'automatic'
        decisions[key] = {'manual_label': label, 'metadata_issue': issue,
                          'review_notes': r.get('review_notes', ''), 'decision_source': source,
                          'automatic_label': auto_label, 'automatic_reason': r.get('automatic_reason', '')}
    return decisions

def merge_records(artist_rows, artists, direct, decisions, allow_unreviewed):
    union = {}; seen_artists = set(); excluded = []; review_counts = Counter()
    def add(mid, row, aid=None, matched=None):
        wid = work_id(row['work_id']); year = row['publication_year']
        if not isinstance(year, int) or not START <= year <= END: raise ValueError('Invalid publication year')
        key = (mid, wid)
        if key in union and union[key]['publication_year'] != year:
            raise ValueError('Conflicting publication years: ' + str(key))
        entry = union.setdefault(key, {'movement_id': mid, 'work_id': wid, 'publication_year': year,
                                      'title': row.get('title', ''), 'artist_ids': set(), 'terms': set()})
        if aid: entry['artist_ids'].add(aid)
        if matched: entry['terms'].update(matched)
    for row in artist_rows:
        aid = row['artist_id']; key = (aid, work_id(row['work_id']))
        if aid not in artists or key in seen_artists: raise ValueError('Unknown/duplicate artist-work')
        seen_artists.add(key)
        if row.get('cleaning_status') != 'include': raise ValueError('Use only cleaned included artist records')
        for mid in artists[aid]: add(mid, row, aid=aid)
    for key, row in direct.items():
        decision = decisions.get(key, {'manual_label': '', 'metadata_issue': 'none', 'review_notes': ''})
        label = decision['manual_label']; review_counts[decision.get('decision_source', 'human' if label else 'unreviewed') + ':' + (label or 'unreviewed')] += 1
        if not label and not allow_unreviewed:
            raise ValueError('Unreviewed direct matches remain; review them or explicitly use --allow-unreviewed')
        if label in {'irrelevant', 'uncertain'} or decision['metadata_issue'] != 'none':
            excluded.append({'movement_id': key[0], 'work_id': key[1], **decision}); continue
        add(key[0], row, matched=row['matched_terms'])
    # A direct-match rejection does not automatically reject an independently
    # retained artist-linked match. Record both paths in the union audit trail.
    return union, excluded, dict(review_counts)

def denominator(folder):
    m = json.loads((folder / 'denominator_manifest.json').read_text(encoding='utf-8'))
    if m['filter'] != FILTER or m['corpus'] != 'core' or m['types'] != ['all'] or m['scale'] != 10000:
        raise ValueError('Denominator scope mismatch')
    raw = folder / 'annual_denominator_response.json'
    if sha(raw) != m['raw_response_sha256']: raise ValueError('Denominator response hash mismatch')
    pages = json.loads(raw.read_text(encoding='utf-8')); groups = [g for page in pages for g in page['group_by']]
    expected = {int(g['key']): int(g['count']) for g in groups}
    rows = csv_rows(folder / 'annual_denominator.csv'); counts = {int(r['year']): int(r['denominator_works']) for r in rows}
    if len(groups) != len(expected) or len(rows) != 220 or set(counts) != set(range(START, END + 1)):
        raise ValueError('Invalid denominator year grid')
    if counts != {y: expected.get(y, 0) for y in range(START, END + 1)} or sum(counts.values()) != m['api_total']:
        raise ValueError('Annual denominator differs from API response')
    if not pages or pages[-1]['meta']['next_cursor'] is not None or any(v < 0 for v in counts.values()):
        raise ValueError('Incomplete denominator response')
    return counts, m

def build(args):
    artists, movements = dictionary(args.dictionary); direct = direct_rows(args, movements)
    decisions = load_reviews(args.reviews, direct, args.use_automatic_labels, args.auto_uncertain)
    if args.use_automatic_labels and args.reviews is None:
        raise ValueError('--use-automatic-labels requires --reviews')
    clean_summary = json.loads((args.clean / 'cleaning_summary.json').read_text(encoding='utf-8'))
    status = csv_rows(args.clean / 'artist_collection_status.csv')
    if len(status) != len(artists) or {r['artist_id'] for r in status} != set(artists):
        raise ValueError('Cleaned artist coverage differs from dictionary')
    if any(r['status'] != 'complete' or r['corpus'] != 'core' or r['topic_scope'] != 'any' or r['art_subfield_id'] != '1213' for r in status):
        raise ValueError('Artist status/scope incomplete')
    artist_path = args.clean / 'artist_work_clean.jsonl'; ar = list(json_rows(artist_path))
    if len(ar) != clean_summary['cleaned_matched_records']: raise ValueError('Cleaned record count mismatch')
    union, excluded, review_counts = merge_records(ar, artists, direct, decisions, args.allow_unreviewed)
    den, manifest = denominator(args.denominator)
    counts = Counter(); detail = []
    excluded_keys = {(e['movement_id'], e['work_id']) for e in excluded}
    for (mid, wid), r in sorted(union.items()):
        via_artist, via_direct = bool(r['artist_ids']), bool(r['terms']); year = r['publication_year']
        counts[(mid, year, 'union')] += 1
        counts[(mid, year, 'artist')] += int(via_artist)
        counts[(mid, year, 'direct')] += int(via_direct)
        counts[(mid, year, 'overlap')] += int(via_artist and via_direct)
        detail.append({**{f: r[f] for f in ['movement_id', 'work_id', 'publication_year', 'title']},
                       'movement_name': movements[mid], 'match_via_artist': via_artist,
                       'match_via_movement': via_direct, 'matched_artist_ids': '; '.join(sorted(r['artist_ids'])),
                       'matched_movement_terms': '; '.join(sorted(r['terms'])),
                       'direct_decision_label': decisions.get((mid, wid), {}).get('manual_label') or ('unreviewed' if (mid, wid) in direct else 'not_applicable'),
                       'direct_decision_source': decisions.get((mid, wid), {}).get('decision_source', 'unreviewed' if (mid, wid) in direct else 'not_applicable'),
                       'automatic_label': decisions.get((mid, wid), {}).get('automatic_label', ''),
                       'direct_match_excluded': (mid, wid) in excluded_keys})
    annual = []
    for mid in sorted(movements):
        for year in range(START, END + 1):
            a,d,o,u = [counts[(mid, year, k)] for k in ['artist', 'direct', 'overlap', 'union']]
            if u != a+d-o or u > den[year]: raise ValueError('Invalid movement union count')
            annual.append({'movement_id': mid, 'movement_name': movements[mid], 'year': year,
                           'artist_linked_works_count': a, 'direct_movement_works_count': d,
                           'overlap_works_count': o, 'union_works_count': u, 'denominator_works': den[year],
                           'movement_works_per_10000': format(u/den[year]*10000, '.12g') if den[year] else '',
                           'normalization_status': 'ok' if den[year] else 'zero_denominator'})
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'movement_work_union.jsonl').open('w', encoding='utf-8') as f:
        for r in detail: f.write(json.dumps(r, ensure_ascii=False) + '\n')
    save_csv(args.output / 'movement_year_union.csv', list(annual[0]), annual)
    exclusion_rows = [{**{k: v for k, v in r.items() if k != 'manual_label'},
                       'decision_label': r['manual_label']} for r in excluded]
    save_csv(args.output / 'direct_match_exclusions.csv',
             ['movement_id', 'work_id', 'decision_label', 'metadata_issue', 'review_notes', 'decision_source', 'automatic_label', 'automatic_reason'], exclusion_rows)
    report = {'definition': 'Distinct works linked via retained artist mentions OR retained movement-name mentions',
              'scope': SCOPE, 'movements': len(movements), 'annual_rows': len(annual),
              'raw_direct_movement_work_pairs': len(direct), 'excluded_direct_pairs': len(excluded),
              'direct_decision_counts': review_counts, 'allow_unreviewed': args.allow_unreviewed,
              'automatic_classification_used': args.use_automatic_labels,
              'automatic_uncertain_policy': args.auto_uncertain if args.use_automatic_labels else None,
              'classification_method': auto_rules.VERSION if args.use_automatic_labels else None,
              'union_movement_work_pairs': len(union), 'denominator_fetched_at_utc': manifest['fetched_at_utc'],
              'hashes': {'dictionary': sha(args.dictionary), 'terms': sha(args.terms),
                         'artist_clean_jsonl': sha(artist_path), 'movement_checkpoint': sha(args.collection/'collection_status.json'),
                         'denominator_csv': sha(args.denominator/'annual_denominator.csv'),
                         'reviews': sha(args.reviews) if args.reviews else None},
              'caveats': ['A direct match exclusion applies to that path only; independent retained artist matches remain.',
                          'Movement term matches may be ambiguous and do not imply primary discussion.',
                          'Movement totals overlap across movements; do not sum them as globally unique works.',
                          'Artist affiliations are dictionary memberships, not publication-year-specific memberships.',
                          'Collection and denominator snapshots may differ in time.']}
    save_json(args.output / 'movement_summary.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['init', 'collect', 'review', 'classify', 'build'])
    p.add_argument('--dictionary', type=Path, default=ROOT/'outputs/artist_dictionary.csv')
    p.add_argument('--terms', type=Path, default=ROOT/'config/openalex_movement_terms.csv')
    p.add_argument('--collection', type=Path, default=ROOT/'data/openalex_movements/core/any/full')
    p.add_argument('--clean', type=Path, default=ROOT/'results/openalex')
    p.add_argument('--denominator', type=Path, default=ROOT/'data/openalex_denominator')
    p.add_argument('--output', type=Path, default=ROOT/'results/openalex_movements')
    p.add_argument('--review-output', type=Path, default=ROOT/'config/openalex_movement_review.csv')
    p.add_argument('--reviews', type=Path)
    p.add_argument('--allow-unreviewed', action='store_true')
    p.add_argument('--use-automatic-labels', action='store_true')
    p.add_argument('--auto-uncertain', choices=['retain', 'exclude'], default='retain')
    p.add_argument('--delay', type=float, default=0.25)
    args = p.parse_args()
    try:
        {'init': init, 'collect': collect, 'review': review, 'classify': classify, 'build': build}[args.command](args)
    except (OSError, ValueError, KeyError, requests.RequestException) as exc:
        p.exit(1, 'FAILED: ' + str(exc) + '\n')

if __name__ == '__main__': main()

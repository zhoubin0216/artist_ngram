"""Fetch annual OpenAlex denominators, then normalize existing cleaned counts.

Run from the artist_ngram repository root. Does not change existing data files.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import time
import requests

START, END = 1800, 2019
BASE = 'from_publication_date:1800-01-01,to_publication_date:2019-12-31,language:en,topics.subfield.id:1213'

def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

def fetch(args):
    types = sorted(set(t.strip() for t in args.types.split(',') if t.strip()))
    if not types or ('all' in types and types != ['all']):
        raise ValueError('Use --types all or a comma-separated list of work types')
    filters = BASE + ('' if types == ['all'] else ',type:' + '|'.join(types))
    key = os.environ.get('OPENALEX_API_KEY', '').strip()
    if not key:
        raise ValueError('Set OPENALEX_API_KEY before fetching')
    pages, counts, cursor = [], {}, '*'
    seen_cursors = set()
    with requests.Session() as session:
        while cursor:
            if cursor in seen_cursors:
                raise ValueError('Repeated API cursor; denominator download incomplete')
            seen_cursors.add(cursor)
            params = {'filter': filters, 'corpus': 'core', 'group_by': 'publication_year',
                      'per_page': 200, 'cursor': cursor}
            for attempt in range(6):
                try:
                    response = session.get('https://api.openalex.org/works', params=params,
                                           headers={'Authorization': 'Bearer ' + key}, timeout=60)
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 5:
                            time.sleep(min(2 ** attempt, 30))
                            continue
                    response.raise_for_status()
                    data = response.json()
                    break
                except requests.RequestException:
                    if attempt == 5:
                        raise
                    time.sleep(min(2 ** attempt, 30))
            if 'group_by' not in data or 'meta' not in data or 'next_cursor' not in data['meta']:
                raise ValueError('Unexpected API response; no files written')
            pages.append(data)
            for group in data['group_by']:
                year, count = int(group['key']), int(group['count'])
                if not START <= year <= END or count < 0 or year in counts:
                    raise ValueError('Invalid or repeated denominator year')
                counts[year] = count
            cursor = data['meta']['next_cursor']
    if sum(counts.values()) != int(pages[0]['meta']['count']):
        raise ValueError('Grouped counts disagree with API total; no files written')
    args.output.mkdir(parents=True, exist_ok=True)
    raw = args.output / 'annual_denominator_response.json'
    raw.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding='utf-8')
    write_csv(args.output / 'annual_denominator.csv', ['year', 'denominator_works', 'denominator_status'],
              ({'year': y, 'denominator_works': counts.get(y, 0),
                'denominator_status': 'positive' if counts.get(y, 0) else 'zero'}
               for y in range(START, END + 1)))
    manifest = {'fetched_at_utc': datetime.now(timezone.utc).isoformat(), 'filter': filters,
                'corpus': 'core', 'types': types, 'scale': 10000,
                'denominator_definition': 'All works meeting corpus/language/date/topic/type filters; no artist name search',
                'api_total': sum(counts.values()),
                'raw_response_sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
    (args.output / 'denominator_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Annual denominator saved to', args.output)

def normalize(args):
    manifest = json.loads((args.denominator / 'denominator_manifest.json').read_text(encoding='utf-8'))
    types = manifest['types']
    expected_filter = BASE + ('' if types == ['all'] else ',type:' + '|'.join(types))
    if manifest['corpus'] != 'core' or manifest['filter'] != expected_filter:
        raise ValueError('Denominator scope does not match this dataset')
    source = args.input / 'artist_year_dense_clean.csv'
    summary = json.loads((args.input / 'cleaning_summary.json').read_text(encoding='utf-8'))
    dense = read_csv(source)
    denominators = read_csv(args.denominator / 'annual_denominator.csv')
    den = {int(r['year']): int(r['denominator_works']) for r in denominators}
    if len(denominators) != 220 or set(den) != set(range(START, END + 1)) or any(v < 0 for v in den.values()):
        raise ValueError('Denominator must contain 220 unique valid years')
    if sum(den.values()) != manifest['api_total']:
        raise ValueError('Denominator CSV disagrees with manifest')
    raw = args.denominator / 'annual_denominator_response.json'
    if hashlib.sha256(raw.read_bytes()).hexdigest() != manifest['raw_response_sha256']:
        raise ValueError('Saved API response hash mismatch')
    selected = {}
    type_rows = read_csv(args.input / 'artist_year_type_clean.csv')
    known_types = {r['type'] for r in type_rows}
    if types != ['all'] and set(types) - known_types:
        raise ValueError('Selected type absent from cleaned data: ' + str(set(types) - known_types))
    for r in type_rows:
        if types == ['all'] or r['type'] in types:
            k = (r['artist_id'], int(r['year']))
            selected[k] = selected.get(k, 0) + int(r['works_count'])
    keys = {(r['artist_id'], int(r['year'])) for r in dense}
    if len(keys) != len(dense) or len(dense) != summary['dense_year_rows']:
        raise ValueError('Duplicate or incomplete dense grid')
    for aid in {r['artist_id'] for r in dense}:
        if {(aid, y) for y in range(START, END + 1)} - keys:
            raise ValueError('Artist missing grid years: ' + aid)
    results = []
    for r in dense:
        if r['is_complete'] != '1':
            raise ValueError('Incomplete artist collection')
        year = int(r['year']); n = selected.get((r['artist_id'], year), 0); d = den[year]
        if n < 0 or n > int(r['works_count']) or (types == ['all'] and n != int(r['works_count'])):
            raise ValueError('Type-specific counts disagree with dense counts')
        if n > d:
            raise ValueError('Artist hits exceed denominator; inspect snapshot/scope for year ' + str(year))
        results.append({**r, 'analysis_works_count': n, 'denominator_works': d,
                        'works_per_10000': format(10000 * n / d, '.12g') if d else '',
                        'normalization_status': 'ok' if d else 'zero_denominator'})
    fields = list(dense[0]) + ['analysis_works_count', 'denominator_works', 'works_per_10000', 'normalization_status']
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / 'artist_year_normalized.csv', fields, results)
    report = {'numerator_definition': 'Distinct retained Work IDs per artist/year within selected types',
              'types': types, 'formula': 'analysis_works_count / denominator_works * 10000',
              'rows': len(results), 'analysis_matches': sum(r['analysis_works_count'] for r in results),
              'zero_denominator_years': [y for y, n in den.items() if not n],
              'denominator_fetched_at_utc': manifest['fetched_at_utc'],
              'source_dense_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'source_type_sha256': hashlib.sha256((args.input / 'artist_year_type_clean.csv').read_bytes()).hexdigest(),
              'denominator_csv_sha256': hashlib.sha256((args.denominator / 'annual_denominator.csv').read_bytes()).hexdigest(),
              'limitations': ['API denominator snapshot may differ in time from artist collection.',
                             'Normalization adjusts indexed annual corpus size, not entity ambiguity or historical coverage bias.',
                             'All-zero or unavailable years need an explicit policy before clustering.']}
    (args.output / 'normalization_summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    f = sub.add_parser('fetch')
    f.add_argument('--types', required=True, help='all or comma-separated type values')
    f.add_argument('--output', type=Path, default=Path('data/openalex_denominator'))
    n = sub.add_parser('normalize')
    n.add_argument('--input', type=Path, default=Path('results/openalex'))
    n.add_argument('--denominator', type=Path, default=Path('data/openalex_denominator'))
    n.add_argument('--output', type=Path, default=Path('results/openalex_normalized'))
    args = p.parse_args()
    try:
        (fetch if args.command == 'fetch' else normalize)(args)
    except (ValueError, OSError, requests.RequestException) as exc:
        p.exit(1, 'FAILED: ' + str(exc) + '\n')

if __name__ == '__main__':
    main()

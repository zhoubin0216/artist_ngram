"""Export a reproducible, artist-balanced manual QA sample from art-scoped OpenAlex JSONL.

This does not automatically discard any records. Mark `manual_label` as relevant,
irrelevant or uncertain after reviewing title/abstract.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]


def sample_rows(folder: Path, sample_size: int, seed: int, max_per_artist: int) -> tuple[list[dict], dict]:
    if sample_size < 1 or max_per_artist < 1:
        raise ValueError('sample_size and max_per_artist must be >=1')
    rng = random.Random(seed)
    flagged, plain = defaultdict(list), defaultdict(list)
    files = sorted((folder / 'raw').glob('*.jsonl'))
    if not files:
        raise FileNotFoundError(f'No raw OpenAlex JSONL under {folder / "raw"}')
    totals = Counter()
    for path in files:
        with path.open(encoding='utf-8') as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f'Invalid JSONL in {path} line {line_no}: {exc}') from exc
                totals['records'] += 1
                qid = row['artist_id']
                if row.get('risk_flags'):
                    flagged[qid].append(row)
                    totals['flagged_records'] += 1
                else:
                    plain[qid].append(row)
    picked = []
    used = set()
    artist_uses = Counter()

    def draw(groups, quota):
        eligible = list(groups)
        rng.shuffle(eligible)
        for k in eligible:
            rng.shuffle(groups[k])
        n = 0
        # Round-robin across artist groups prevents popular names from dominating.
        while n < quota and eligible:
            next_eligible = []
            for artist in eligible:
                while groups[artist] and ((artist, groups[artist][-1]['work_id']) in used):
                    groups[artist].pop()
                if artist_uses[artist] < max_per_artist and groups[artist] and n < quota:
                    row = groups[artist].pop()
                    picked.append(row)
                    used.add((artist, row['work_id']))
                    artist_uses[artist] += 1
                    n += 1
                if groups[artist] and artist_uses[artist] < max_per_artist:
                    next_eligible.append(artist)
            eligible = next_eligible
        return n

    target_flagged = sample_size // 2
    a = draw(flagged, target_flagged)
    b = draw(plain, sample_size - a)
    if len(picked) < sample_size:
        draw(flagged, sample_size - len(picked))
    if len(picked) < sample_size:
        draw(plain, sample_size - len(picked))
    rng.shuffle(picked)
    return picked, {'source_artists': len(files), 'source_records': totals['records'],
                    'source_flagged_records': totals['flagged_records'],
                    'sampled': len(picked), 'sample_flagged': sum(bool(x.get('risk_flags')) for x in picked),
                    'sample_unflagged': sum(not x.get('risk_flags') for x in picked),
                    'artists_in_sample': len({x['artist_id'] for x in picked}),
                    'seed': seed, 'max_per_artist': max_per_artist}


def export_sample(rows: list[dict], output: Path):
    columns = ['artist_id', 'artist_name', 'work_id', 'publication_year', 'type',
               'title', 'abstract_excerpt', 'match_in_title', 'match_in_abstract',
               'primary_subfield', 'risk_flags', 'manual_label','metadata_issue', 'review_notes']
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                'artist_id': row['artist_id'], 'artist_name': row['artist_name'],
                'work_id': row['work_id'], 'publication_year': row['publication_year'],
                'type': row['type'], 'title': row['title'],
                'abstract_excerpt': (row.get('abstract') or '')[:1200],
                'match_in_title': row['match_in_title'],
                'match_in_abstract': row['match_in_abstract'],
                'primary_subfield': row.get('primary_subfield', ''),
                'risk_flags': ';'.join(row.get('risk_flags') or []),
                'manual_label': '','metadata_issue': 'none', 'review_notes': ''})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir', type=Path,
                   default=ROOT / 'data/openalex_art/core/any/pilot')
    p.add_argument('--size', type=int, default=100)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--max-per-artist', type=int, default=10)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    rows, stats = sample_rows(args.work_dir, args.size, args.seed, args.max_per_artist)
    path = args.output or (args.work_dir / 'audit_sample.csv')
    export_sample(rows, path)
    print(json.dumps({'file': str(path), **stats}, indent=2))


if __name__ == '__main__':
    main()

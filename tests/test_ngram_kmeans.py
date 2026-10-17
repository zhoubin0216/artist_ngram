from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'scripts' / 'ml'
sys.path.insert(0, str(SCRIPT_DIR))
import cluster_ngram as m


class NgramKMeansTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'outputs' / 'ngram_artists_full'
        (self.source / 'totalcounts').mkdir(parents=True)
        for n in (2, 3):
            (self.source / 'totalcounts' / f'totalcounts-{n}').write_text(
                '\t'.join(f'{y},1000000000,100,10' for y in range(1800, 2020)), encoding='utf-8')
        artists = []
        records = []
        # 3 distinct temporal patterns, 12 artists each; plus one deliberately absent artist.
        for i in range(36):
            group = i // 12
            aid = f'Q{i+1000}'
            a = {'qid': aid, 'query': f'Artist Example {i}', 'movements': ['impressionism'],
                 'birth_year': 1800, 'death_year': 2000, 'n': (2 if i % 2 else 3), 'status': 'matched'}
            cnt = 0
            for y in range(1800, 2020):
                dec = (y - 1800) // 10
                if group == 0:
                    num = 2 + dec * 2
                elif group == 1:
                    num = 2 + (21-dec) * 2
                else:
                    num = 2 + int(33 * max(0.0, 1-abs(dec-10)/7))
                num += (i % 3)
                records.append({'artist_id': aid, 'query': a['query'], 'n': a['n'],
                                'year': y, 'match_count': num, 'volume_count': 1})
                cnt += num
            a.update({'rows': 220, 'match_count_sum': cnt})
            artists.append(a)
        artists.append({'qid': 'Q999999', 'query': 'Not Found Person', 'movements': [],
                        'birth_year': 1850, 'death_year': 1900, 'n': 2,
                        'status': 'not_found', 'rows': 0, 'match_count_sum': 0})
        (self.source / 'artists.json').write_text(json.dumps(artists), encoding='utf-8')
        with (self.source / 'artist_year_counts.jsonl').open('w', encoding='utf-8') as f:
            for rec in records:
                f.write(json.dumps(rec) + '\n')
        (self.source / 'report.json').write_text(json.dumps({
            'run_status': 'complete', 'failed_shards': {}, 'artist_count': len(artists),
            'start_year': 1800, 'end_year': 2019, 'row_count': len(records)}), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def args(self, output='results/ml/test'):
        return SimpleNamespace(root=str(self.root), output=output, assume_unlisted_zero=True,
                               min_nonzero_decades=4, min_total_matches=20,
                               min_cluster_size=5, max_k=5, seed=42, stability_runs=3)

    def test_end_to_end_and_repeatable(self):
        summary = m.run(self.args())
        self.assertEqual(summary['all_artists'], 37)
        self.assertEqual(summary['eligible_artists'], 36)
        self.assertEqual(summary['not_found_artists'], 1)
        self.assertIn(summary['k_selected'], [2, 3, 4, 5])
        path = self.root / 'results/ml/test/cluster_assignments.csv'
        first = m.sha256(path)
        m.run(self.args())
        self.assertEqual(m.sha256(path), first)
        self.assertTrue((self.root / 'results/ml/test/figures/cluster_centers.png').exists())

    def test_explicit_zero_assumption_required(self):
        artists, sparse, totals, *_ = m.load_inputs(self.root)
        with self.assertRaisesRegex(ValueError, 'assume-unlisted-zero'):
            m.make_decade_features(artists, sparse, totals, min_nonzero_decades=4,
                                  min_total_matches=20, assume_unlisted_zero=False)

    def test_incomplete_collection_rejected(self):
        f = self.source / 'report.json'
        obj = json.loads(f.read_text())
        obj['run_status'] = 'partial'
        f.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError, 'not complete'):
            m.load_inputs(self.root)

    def test_duplicate_artist_year_rejected(self):
        data = self.source / 'artist_year_counts.jsonl'
        with data.open('r', encoding='utf-8') as handle:
            line = handle.readline()
        with data.open('a', encoding='utf-8') as f:
            f.write(line)
        with self.assertRaisesRegex(ValueError, 'duplicate artist-year'):
            m.load_inputs(self.root)

    def test_sparse_missing_decade_is_explicit_zero(self):
        records_file = self.source / 'artist_year_counts.jsonl'
        data = [json.loads(s) for s in records_file.read_text(encoding='utf-8').splitlines()]
        removed = [r for r in data if r['artist_id'] == 'Q1000' and 1800 <= r['year'] <= 1809]
        kept = [r for r in data if r not in removed]
        with records_file.open('w', encoding='utf-8') as f:
            for r in kept:
                f.write(json.dumps(r) + '\n')
        artist_file = self.source / 'artists.json'
        artists = json.loads(artist_file.read_text())
        artists[0]['rows'] -= len(removed)
        artists[0]['match_count_sum'] -= sum(r['match_count'] for r in removed)
        artist_file.write_text(json.dumps(artists), encoding='utf-8')
        report_file = self.source / 'report.json'
        report = json.loads(report_file.read_text())
        report['row_count'] -= len(removed)
        report_file.write_text(json.dumps(report), encoding='utf-8')
        a, sparse, total, *_ = m.load_inputs(self.root)
        rows, included, _ = m.make_decade_features(a, sparse, total,
            min_nonzero_decades=4, min_total_matches=20, assume_unlisted_zero=True)
        artist = next(row for row in rows if row['artist_id'] == 'Q1000')
        self.assertEqual(artist['rate_1800'], 0.0)
        self.assertGreater(artist['rate_1810'], 0.0)
        self.assertIn('Q1000', {r['artist_id'] for r in included})

    def test_exact_gram_denominator_needed(self):
        (self.source / 'totalcounts' / 'totalcounts-3').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing exact-n denominator'):
            m.load_inputs(self.root)


if __name__ == '__main__':
    unittest.main()

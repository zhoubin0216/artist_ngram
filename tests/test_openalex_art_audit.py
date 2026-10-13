import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'audit_openalex_art.py'
spec = importlib.util.spec_from_file_location('audit_openalex_art', SOURCE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class TestAudit(unittest.TestCase):
    def test_balanced_sample_and_csv(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / 'raw').mkdir()
            for artist in range(4):
                with (folder / 'raw' / f'Q{artist}.jsonl').open('w') as out:
                    for j in range(4):
                        record = {'artist_id': f'Q{artist}', 'artist_name': f'Artist {artist}',
                                  'work_id': f'W{artist}-{j}', 'publication_year': 1900 + j,
                                  'type': 'article', 'title': f'Artist {artist} study', 'abstract': 'ok',
                                  'match_in_title': True, 'match_in_abstract': False,
                                  'primary_subfield': 'Visual Arts and Performing Arts',
                                  'risk_flags': ['flag'] if j % 2 else []}
                        out.write(json.dumps(record) + '\n')
            sampled, stats = audit.sample_rows(folder, 8, seed=42, max_per_artist=3)
            self.assertEqual(len(sampled), 8)
            self.assertGreater(stats['sample_flagged'], 0)
            self.assertGreater(stats['sample_unflagged'], 0)
            self.assertEqual(len({(x['artist_id'], x['work_id']) for x in sampled}), 8)
            path = folder / 'sample.csv'
            audit.export_sample(sampled, path)
            with path.open(encoding='utf-8-sig') as f:
                labels = list(csv.DictReader(f))
            self.assertEqual(len(labels), 8)
            self.assertEqual(labels[0]['manual_label'], '')
            self.assertTrue(all(row['metadata_issue'] == 'none' for row in labels))

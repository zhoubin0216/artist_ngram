"""Run from repository root: python -m unittest discover -s tests -p test_openalex_collector.py -v"""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'collect_openalex.py'
spec = importlib.util.spec_from_file_location('collect_openalex', SOURCE)
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)


def indexed(sentence):
    result = {}
    for pos, word in enumerate(sentence.split()):
        result.setdefault(word, []).append(pos)
    return result


ART = {'field': {'display_name': 'Arts and Humanities'},
       'subfield': {'id': 'https://openalex.org/subfields/1213',
                    'display_name': 'Visual Arts and Performing Arts'}}
HISTORY = {'field': {'display_name': 'Arts and Humanities'},
           'subfield': {'id': 'https://openalex.org/subfields/1202',
                        'display_name': 'History'}}
PHILOSOPHY = {'field': {'display_name': 'Arts and Humanities'},
              'subfield': {'id': 'https://openalex.org/subfields/1211',
                           'display_name': 'Philosophy'}}


def work(identifier, title, abstract='', year=1882, topics=None, primary_topic=None,
         lang='en', kind='article'):
    topics = [ART] if topics is None else topics
    return {'id': identifier, 'doi': None, 'title': title,
            'abstract_inverted_index': indexed(abstract),
            'publication_year': year, 'language': lang, 'type': kind,
            'topics': topics, 'primary_topic': primary_topic or (topics[0] if topics else {})}


class DummySession:
    pass


class Tests(unittest.TestCase):
    def setUp(self):
        self.artist = {'artist_id': 'Q296', 'canonical_name': 'Claude Monet', 'birth_year': '1840'}

    def test_exact_full_name_only(self):
        self.assertEqual(oa.restore_abstract(indexed('Claude Monet and Claude Monet')),
                         'Claude Monet and Claude Monet')
        self.assertTrue(oa.contains_full_name('Claude-Monet studies', 'Claude Monet'))
        self.assertFalse(oa.contains_full_name('Monet paintings', 'Claude Monet'))
        self.assertFalse(oa.contains_full_name('Claud Monet', 'Claude Monet'))
        self.assertFalse(oa.contains_full_name('Claude Monetary', 'Claude Monet'))

    def test_art_scope_filter(self):
        self.assertEqual(oa.art_filter('any'), 'topics.subfield.id:1213')
        self.assertEqual(oa.art_filter('primary'), 'primary_topic.subfield.id:1213')
        with self.assertRaises(ValueError):
            oa.art_filter('unknown')
        w = work('W1', 'Claude Monet', topics=[HISTORY, ART], primary_topic=HISTORY)
        self.assertTrue(oa.art_membership(w)[0])
        self.assertFalse(oa.art_membership(w)[1])
        self.assertIsNotNone(oa.make_row(w, self.artist, 'any'))
        self.assertIsNone(oa.make_row(w, self.artist, 'primary'))
        self.assertIsNone(oa.make_row(work('W2', 'Claude Monet', topics=[PHILOSOPHY]), self.artist))

    def test_local_revalidation_and_quality_flags(self):
        row = oa.make_row(work('W1', 'Claude Monet', year=1801, topics=[HISTORY, ART], primary_topic=HISTORY), self.artist)
        self.assertIn('publication_before_artist_birth', row['risk_flags'])
        self.assertIn('visual_arts_secondary_topic_only', row['risk_flags'])
        self.assertEqual(row['topic_scope'], 'any')
        self.assertIsNone(oa.make_row(work('W2', 'Monet', topics=[ART]), self.artist))
        self.assertIsNone(oa.make_row(work('W3', 'Claude Monet', year=2025), self.artist))
        self.assertIsNone(oa.make_row(work('W4', 'Claude Monet', lang='fr'), self.artist))

    def test_archive_cursor_dedupe_and_complete_status(self):
        a = work('https://openalex.org/W1', 'Claude Monet')
        b = work('https://openalex.org/W2', 'Art history', 'About Claude Monet', topics=[HISTORY, ART], primary_topic=HISTORY)
        c = work('https://openalex.org/W3', 'Not the requested name')
        pages = [
            {'meta': {'count': 4, 'next_cursor': 'after1'}, 'results': [a, a]},
            {'meta': {'count': 4, 'next_cursor': None}, 'results': [b, c]},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'raw' / 'Q296.jsonl'
            path.parent.mkdir()
            with patch.object(oa, 'request_page', side_effect=pages) as call:
                result = oa.collect_one(DummySession(), self.artist, path, '', 0, None)
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(result['accepted_works'], 2)
                self.assertEqual(result['api_candidates_saved'], 4)
                self.assertEqual(call.call_count, 2)
                params = call.call_args_list[0][0][1]
                self.assertEqual(params['search.title_and_abstract'], '"Claude Monet"')
                self.assertIn('topics.subfield.id:1213', params['filter'])
                self.assertIn('language:en', params['filter'])
                self.assertEqual(params['corpus'], 'core')
                self.assertNotIn('type:', params['filter'])
            matches = [json.loads(l) for l in path.read_text().splitlines()]
            self.assertEqual(len(matches), 2)
            archives = [json.loads(l) for l in (Path(d) / 'candidates' / 'Q296.jsonl').read_text().splitlines()]
            self.assertEqual(len(archives), 4)
            self.assertEqual(sum(1 for a in archives if not a['locally_verified']), 1)
            with patch.object(oa, 'request_page', return_value=pages[0]):
                truncated = oa.collect_one(DummySession(), self.artist, path, '', 0, 1)
            self.assertEqual(truncated['status'], 'partial')
            self.assertFalse(truncated['candidate_archive_complete'])
            with patch.object(oa, 'request_page', return_value={'meta': {'count': 0, 'next_cursor': None}, 'results': []}):
                all_corpus = oa.collect_one(DummySession(), self.artist, path, '', 0, None, 'primary', 'all')
                self.assertEqual(all_corpus['corpus'], 'all')

    def test_summary_is_art_specific(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            (directory / 'raw').mkdir()
            (directory / 'collection_status.json').write_text(json.dumps({
                'Q296': {'artist_id': 'Q296', 'artist_name': 'Claude Monet', 'status': 'complete',
                         'pages': 1, 'api_search_hits': 2, 'accepted_works': 2,
                         'unverified_candidates': 0, 'api_candidates_saved': 2,
                         'topic_scope': 'any', 'art_subfield_id': '1213', 'corpus': 'core',
                         'candidate_archive_complete': True}}))
            w1 = oa.make_row(work('W1', 'Claude Monet'), self.artist)
            w2 = oa.make_row(work('W2', 'Claude Monet', topics=[HISTORY, ART], primary_topic=HISTORY), self.artist)
            with (directory / 'raw' / 'Q296.jsonl').open('w') as f:
                for w in (w1, w2):
                    f.write(json.dumps(w) + '\n')
            oa.summarize(directory, [self.artist])
            with (directory / 'artist_year.csv').open(encoding='utf-8-sig') as f:
                years = list(csv.DictReader(f))
            self.assertEqual(len(years), 1)
            self.assertEqual(int(years[0]['art_works_count']), 2)
            self.assertEqual(int(years[0]['primary_visual_arts_count']), 1)
            self.assertEqual(int(years[0]['flagged_works_count']), 1)  # secondary-only art topic


if __name__ == '__main__':
    unittest.main()

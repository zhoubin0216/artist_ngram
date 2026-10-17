"""Focused synthetic regression tests for Stage A3; no network or original files."""
from __future__ import annotations
import csv
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'ml'))
sys.path.insert(0, str(ROOT / 'tests'))
import cluster_ngram_death_aligned as a3
import test_ngram_kmeans as base_fixture


class A3Tests(unittest.TestCase):
    def setUp(self):
        fixture = base_fixture.NgramKMeansTests('test_end_to_end_and_repeatable')
        fixture.setUp()
        self.fixture = fixture
        self.root = fixture.root
        af = self.root / 'outputs/ngram_artists_full/artists.json'
        items = json.loads(af.read_text(encoding='utf-8'))
        for obj in items:
            obj['death_year'] = 1900 if obj['status'] == 'matched' else 1900
        af.write_text(json.dumps(items), encoding='utf-8')
        a2file = self.root / 'results/ml/ngram_lifecycle_audit/artist_lifecycle_audit.csv'
        oldfile = self.root / 'results/ml/ngram_kmeans/cluster_assignments.csv'
        a2file.parent.mkdir(parents=True, exist_ok=True)
        oldfile.parent.mkdir(parents=True, exist_ok=True)
        with a2file.open('w', encoding='utf-8', newline='') as fw, oldfile.open('w', encoding='utf-8', newline='') as fb:
            aw = csv.DictWriter(fw, fieldnames=['artist_id','cluster','birth_year','death_year','identity_warning','warning_reason'])
            bw = csv.DictWriter(fb, fieldnames=['artist_id','cluster_id'])
            aw.writeheader(); bw.writeheader()
            for i in range(36):
                aid = f'Q{i+1000}'
                group = int(i//12)%2
                aw.writerow({'artist_id':aid,'cluster':group,'birth_year':1800,'death_year':1900,
                             'identity_warning': i < 3, 'warning_reason':'review' if i<3 else ''})
                bw.writerow({'artist_id':aid, 'cluster_id':group})
        self.args = SimpleNamespace(root=str(self.root),
            a2_csv='results/ml/ngram_lifecycle_audit/artist_lifecycle_audit.csv',
            output='results/ml/ngram_death_aligned', assume_unlisted_zero=True,
            min_nonzero_bins=3, min_total_matches=20,
            min_cluster_size=3, max_k=3, stability_runs=2, seed=42)

    def tearDown(self):
        self.fixture.tearDown()

    def test_bin_rates_use_exact_ten_annual_years(self):
        den={(2,y): 100 for y in range(1870,1950)}
        obs={1890:4, 1900:6, 1909:10, 1910:2}
        rate, counts = a3.bin_rates(obs,2,1900,den)
        self.assertEqual(len(rate), 8)
        self.assertEqual(counts[3],16)
        self.assertEqual(counts[4],2)
        self.assertEqual(rate[3],16/1000*1e9)

    def test_identity_warning_boolean_parsing(self):
        self.assertTrue(a3.flag('True'))
        self.assertFalse(a3.flag('False'))
        with self.assertRaises(ValueError):
            a3.flag('uncertain')

    def test_coverage_and_naming(self):
        artists,sparse,totals,*_=a3.a1.load_inputs(self.root)
        a2=a3.read_unique_csv(self.root/self.args.a2_csv,'artist_id')
        baseline=a3.read_unique_csv(self.root/'results/ml/ngram_kmeans/cluster_assignments.csv','artist_id')
        allowed,rows=a3.compute_cohort(artists,a2,baseline,sparse,totals,
             min_nonzero_bins=3,min_total_matches=20)
        self.assertEqual(len(rows),36)
        self.assertEqual(len(allowed),36)
        self.assertEqual(sum(x['identity_warning'] for x in allowed),3)
        self.assertTrue(all(x['death_year']==1900 for x in allowed))

    def test_no_overwriting_and_actual_run(self):
        old_file=self.root/'results/ml/ngram_kmeans/cluster_assignments.csv'
        old_sha=a3.a1.sha256(old_file)
        r,path=a3.run(self.args)
        self.assertEqual(r['selection']['eligible_for_both_models'],36)
        self.assertEqual(r['selection']['complete_window_artists'],36)
        self.assertEqual(r['selection']['unflagged_eligible'],33)
        self.assertTrue((path/'figures/cluster_comparison_heatmap.png').exists())
        self.assertTrue((path/'death_aligned_features.csv').exists())
        with (path/'death_aligned_assignments.csv').open(encoding='utf-8-sig') as f:
            self.assertEqual(sum(1 for _ in csv.DictReader(f)),36)
        self.assertEqual(old_sha,a3.a1.sha256(old_file))
        # Allocate a different subdirectory even when invoked again at same second.
        path2=a3.alloc_output(self.root/self.args.output)
        self.assertNotEqual(path,path2)

    def test_window_cutoffs_are_not_zero_padded(self):
        artists,sparse,totals,*_=a3.a1.load_inputs(self.root)
        for a in artists:
            if a['qid']=='Q1000':
                a['death_year']=1990  # plus 49 beyond 2019
        a2=a3.read_unique_csv(self.root/self.args.a2_csv,'artist_id')
        baseline=a3.read_unique_csv(self.root/'results/ml/ngram_kmeans/cluster_assignments.csv','artist_id')
        a2['Q1000']['death_year']='1990'
        selected,elig=a3.compute_cohort(artists,a2,baseline,sparse,totals,
             min_nonzero_bins=3,min_total_matches=20)
        self.assertEqual(len(selected),35)
        row=next(x for x in elig if x['artist_id']=='Q1000')
        self.assertFalse(row['complete_window'])
        self.assertIn('window_outside_1800_2019',row['exclusion_reason'])

if __name__ == '__main__': unittest.main()

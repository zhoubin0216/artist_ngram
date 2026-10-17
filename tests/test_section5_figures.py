import importlib.util
from pathlib import Path
import tempfile
import unittest

import pandas as pd

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'ml' / 'build_section5_figures.py'
spec = importlib.util.spec_from_file_location('build_section5_figures', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def minimal_tables():
    artist_ids = ['Q1', 'Q2']
    base=[dict(artist_id=x, canonical_name='Artist '+x, death_year=1954,
               identity_warning=False, death_cluster_id=i,
               openalex_signal_sufficient=True, openalex_window_works=8)
          for i,x in enumerate(artist_ids)]
    ab=[]; cb=[]
    for i,id_ in enumerate(artist_ids):
        for b in m.BINS:
            ab.append(dict(artist_id=id_,death_cluster_id=i,relative_bin_start=b,
                           works_count=1,denominator_works=1000,years_observed=10,works_per_10000=10.0))
            cb.append(dict(artist_id=id_,cluster_id=i,accepted_mapping=True,
                           relative_bin_start=b,confirmed_sale_count=2,catalog_entry_count=1,total_record_count=3,
                           record_state='accepted_mapping_dated_index'))
    ba=pd.DataFrame(base)
    for b in m.BINS:
        ba[f'ngram_rate_rel_{b}'] = 3.2
        ba[f'openalex_rate_rel_{b}'] = 10.0
    return {
        'a3_assign':pd.DataFrame([dict(artist_id=id_,death_cluster_id=i) for i,id_ in enumerate(artist_ids)]),
        'a3_features':pd.DataFrame([dict(artist_id=id_,canonical_name='Artist '+id_,
                                    death_year=1954,identity_warning=False,**{f'rate_rel_{b}':3.2 for b in m.BINS}) for id_ in artist_ids]),
        'b_summary':ba,
        'b_decades':pd.DataFrame(ab),
        'c_coverage':pd.DataFrame([dict(artist_id=id_,canonical_name='Artist '+id_,death_year=1954,
                                   cluster_id=i,accepted_mapping=True,getty_window_signal_sufficient=True,
                                   identity_warning=False,window_dated_events=24) for i,id_ in enumerate(artist_ids)]),
        'c_decades':pd.DataFrame(cb)
    }

class FigureTests(unittest.TestCase):
    def test_truth(self):
        self.assertTrue(m.to_bool('True'))
        self.assertFalse(m.to_bool('False'))
        with self.assertRaises(ValueError):m.to_bool('maybe')
    def test_duplicate_keys(self):
        df=pd.DataFrame({'artist_id':['Q1','Q1']})
        with self.assertRaises(ValueError):m.require_unique(df,['artist_id'],'test')
    def test_bin_guard(self):
        df=pd.DataFrame({'artist_id':['Q1']*7,'relative_bin_start':m.BINS[:7]})
        with self.assertRaises(ValueError):m.check_bins(df,'Q1','test')
    def test_two_cases_pass(self):
        rows,meta=m.build_case_rows(['Q1','Q2'],minimal_tables())
        self.assertEqual(len(rows),16)
        self.assertEqual(sum(rows.getty_confirmed_title_transfers),32)
        self.assertEqual(meta[0]['death_year'],meta[1]['death_year'])
    def test_mismatched_rate_fails(self):
        t=minimal_tables();t['b_summary'].loc[0,'ngram_rate_rel_0']=987
        with self.assertRaises(ValueError):m.build_case_rows(['Q1','Q2'],t)
    def test_undated_and_unmatched_not_assumed_zero(self):
        t=minimal_tables();t['c_decades'].loc[0,'record_state']='no_accepted_mapping'
        with self.assertRaises(ValueError):m.build_case_rows(['Q1','Q2'],t)
    def test_output_run_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=m.new_run_dir(Path(tmp)); b=m.new_run_dir(Path(tmp))
            self.assertNotEqual(a,b)
            self.assertTrue((a/'figures').is_dir())
    def test_same_year_guard(self):
        t=minimal_tables();t['a3_features'].loc[1,'death_year']=1956
        with self.assertRaises(ValueError):m.build_case_rows(['Q1','Q2'],t)

if __name__=='__main__':unittest.main()

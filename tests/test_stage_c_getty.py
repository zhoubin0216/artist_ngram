import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

MODULE_FILE = Path(__file__).resolve().parents[1] / 'scripts' / 'ml' / 'validate_ngram_with_getty.py'
SPEC = importlib.util.spec_from_file_location('stage_c_getty', MODULE_FILE)
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class StageCGettyTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.a3 = self.root / 'a3'
        self.getty = self.root / 'getty'
        self.output = self.root / 'outputs'
        self.a3.mkdir(); self.getty.mkdir()
        self.ids = ['Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6']
        pd.DataFrame({'artist_id': self.ids, 'death_cluster_id':[0,0,0,1,1,1]}).to_csv(
            self.a3/'death_aligned_assignments.csv', index=False)
        pd.DataFrame({'artist_id':self.ids,
                      'canonical_name':[f'Artist {v}' for v in self.ids],
                      'birth_year':[1860]*6,'death_year':[1900]*6,
                      'identity_warning':[False,True,False,False,False,False]}).to_csv(
            self.a3/'death_aligned_features.csv',index=False)
        pd.DataFrame({'artist_id':self.ids,'getty_person_uri':['uri1','uri2','', 'uri4','',''],
                      'manual_review':[False,False,True,False,True,True],
                      'match_confidence':[.9,.9,0,.9,0,0]}).to_csv(
            self.getty/'getty_artist_mapping.csv',index=False)
        # Q1: 3 dated records, -30, +49 (boundary test) and 1 undated.
        # Q2: one dated record at +50 is OUTSIDE the window, still in annual.
        # Q4: all events are undated.
        self.events = pd.DataFrame([
            {'artist_id':'Q1','event_year':1870,'event_type':'confirmed_sale'},
            {'artist_id':'Q1','event_year':1949,'event_type':'catalog_entry'},
            {'artist_id':'Q1','event_year':1949,'event_type':'confirmed_sale'},
            {'artist_id':'Q1','event_year':None,'event_type':'catalog_entry'},
            {'artist_id':'Q2','event_year':1950,'event_type':'catalog_entry'},
            {'artist_id':'Q4','event_year':None,'event_type':'confirmed_sale'},
        ])
        self.events.to_csv(self.getty/'getty_events.csv',index=False)
        self.annual = pd.DataFrame([
            {'artist_id':'Q1','event_year':1870,'confirmed_sale_count':1,'catalog_entry_count':0,'total_record_count':1},
            {'artist_id':'Q1','event_year':1949,'confirmed_sale_count':1,'catalog_entry_count':1,'total_record_count':2},
            {'artist_id':'Q2','event_year':1950,'confirmed_sale_count':0,'catalog_entry_count':1,'total_record_count':1},
        ])
        self.annual.to_csv(self.getty/'getty_artist_year.csv',index=False)
        self.b = self.root/'stage_b.csv'
        pd.DataFrame({'artist_id':self.ids,'death_cluster_id':[0,0,0,1,1,1],
                      'openalex_any':['True','False','True','True','False','False'],
                      'openalex_signal_sufficient':['True','False','False','True','False','False']}).to_csv(self.b,index=False)

    def tearDown(self): self.t.cleanup()

    def run_c(self, **kwargs):
        return m.run_analysis(a3_dir=self.a3, getty_dir=self.getty,output_root=self.output,
                              stage_b=kwargs.get('stage_b',self.b),
                              min_window_records=kwargs.get('min_window_records',3),
                              min_nonzero_bins=kwargs.get('min_nonzero_bins',2))

    def test_expected_coverage_and_undated(self):
        out, summary = self.run_c()
        self.assertEqual(summary['a3_artists'],6)
        self.assertEqual(summary['a3_accepted_getty_mappings'],3)
        self.assertEqual(summary['a3_with_dated_event_in_window'],1)
        self.assertEqual(summary['a3_with_getty_trend_signal'],1)
        self.assertEqual(summary['getty_full_dataset_events_without_year'],2)
        self.assertEqual(summary['three_source_any_overlap'],1)
        self.assertEqual(summary['three_source_both_signal_sufficient'],1)
        frame=pd.read_csv(out/'getty_artist_coverage.csv')
        self.assertEqual(frame.set_index('artist_id').loc['Q4','getty_status'],'events_all_undated')
        self.assertEqual(frame.set_index('artist_id').loc['Q2','getty_status'],'dated_events_outside_window')
        self.assertEqual(frame.set_index('artist_id').loc['Q3','getty_status'],'not_accepted_mapping')
        self.assertFalse(pd.isna(frame.set_index('artist_id').loc['Q1','window_sales']))
        self.assertTrue((out/'figures/getty_coverage_by_cluster.png').exists())

    def test_relative_bins_and_missing_vs_zero(self):
        out,_=self.run_c(stage_b=None)
        rows=pd.read_csv(out/'getty_relative_decades.csv')
        q1=rows[rows.artist_id=='Q1'].set_index('relative_bin_start')
        self.assertEqual(q1.loc[-30,'confirmed_sale_count'],1)
        self.assertEqual(q1.loc[40,'confirmed_sale_count'],1)
        self.assertEqual(q1.loc[40,'catalog_entry_count'],1)
        self.assertEqual(q1['total_record_count'].sum(),3)
        self.assertTrue(rows[rows.artist_id=='Q3']['confirmed_sale_count'].isna().all())
        self.assertTrue((rows[rows.artist_id=='Q2']['total_record_count']==0).all())
        self.assertFalse((out/'three_source_overlap.csv').exists())

    def test_invalid_annual_raises(self):
        self.annual.loc[0,'confirmed_sale_count']=9
        self.annual.loc[0,'total_record_count']=9
        self.annual.to_csv(self.getty/'getty_artist_year.csv',index=False)
        with self.assertRaisesRegex(ValueError,'annual counts disagree'):
            self.run_c()

    def test_bad_mapping_status_raises(self):
        x=pd.read_csv(self.getty/'getty_artist_mapping.csv')
        x.loc[x.artist_id=='Q3','getty_person_uri']='forbidden'
        x.to_csv(self.getty/'getty_artist_mapping.csv',index=False)
        with self.assertRaisesRegex(ValueError,'accepted URI with review'):
            self.run_c()

    def test_second_run_does_not_overwrite(self):
        first,_=self.run_c()
        second,_=self.run_c()
        self.assertNotEqual(first,second)
        self.assertTrue((first/'comparison_summary.json').exists())
        self.assertTrue((second/'comparison_summary.json').exists())

    def test_wilson_bounds(self):
        lo,hi=m.wilson_interval(1,3)
        self.assertTrue(0<=lo<1/3<hi<=1)
        self.assertEqual(m.wilson_interval(0,0),(None,None))


if __name__=='__main__': unittest.main()

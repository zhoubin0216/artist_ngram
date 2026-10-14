"""Offline movement tests. Place under tests/ in artist_ngram."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'scripts/openalex_movements.py'
if not SCRIPT.exists(): SCRIPT = Path(__file__).with_name('openalex_movements.py')
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('movements', SCRIPT)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def row(aid, wid, year=1900):
    return {'artist_id': aid, 'work_id': wid, 'publication_year': year,
            'title': 'Study', 'cleaning_status': 'include'}

def direct(wid, year=1900):
    return {('M05', wid): {'work_id': wid, 'publication_year': year,
                          'title': 'Impressionism', 'matched_terms': {'Impressionism'}}}

class MovementTests(unittest.TestCase):
    def test_automatic_visual_vs_other_sense(self):
        a={'movement_name':'Impressionism','title':'Impressionism and painting','abstract':'','matched_terms':'Impressionism'}
        self.assertEqual(m.auto_rules.label(a)['automatic_label'],'relevant')
        a.update(movement_name='Realism',title='Scientific realism',matched_terms='Realism')
        self.assertEqual(m.auto_rules.label(a)['automatic_label'],'irrelevant')

    def test_generic_symbolism_is_uncertain(self):
        a={'movement_name':'Symbolism','title':'Animal symbolism in painting','abstract':'','matched_terms':'Symbolism'}
        self.assertEqual(m.auto_rules.label(a)['automatic_label'],'uncertain')

    def test_parent_movement_substring_not_assumed(self):
        a={'movement_name':'Impressionism','title':'Post-Impressionism','abstract':'','matched_terms':'Impressionism'}
        self.assertEqual(m.auto_rules.label(a)['automatic_label'],'uncertain')

    def test_human_label_overrides_automation(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'reviews.csv'
            r={'movement_id':'M05','work_id':'W1','manual_label':'irrelevant','metadata_issue':'none',
               'automatic_label':'relevant','classification_method':m.auto_rules.VERSION,'automatic_reason':'wrong'}
            m.save_csv(path,list(r),[r])
            d=m.load_reviews(path,direct('W1'),True)
            self.assertEqual(d[('M05','W1')]['decision_source'],'human')
            self.assertEqual(d[('M05','W1')]['manual_label'],'irrelevant')

    def test_automatic_uncertain_policy_and_source(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'reviews.csv'
            match=direct('W1');match[('M05','W1')]['movement_name']='Symbolism'
            match[('M05','W1')]['matched_terms']={'Symbolism'}
            match[('M05','W1')]['title']='Animal symbolism'
            evidence={**match[('M05','W1')],'matched_terms':'Symbolism'}
            r={'movement_id':'M05','work_id':'W1','manual_label':'','metadata_issue':'none',**m.auto_rules.label(evidence)}
            m.save_csv(path,list(r),[r])
            d=m.load_reviews(path,match,True,'retain')
            self.assertEqual(d[('M05','W1')]['manual_label'],'')
            self.assertEqual(d[('M05','W1')]['decision_source'],'automatic')
            d=m.load_reviews(path,match,True,'exclude')
            self.assertEqual(d[('M05','W1')]['manual_label'],'uncertain')
    def test_multiple_artists_and_direct_match_count_once(self):
        union, excluded, stats = m.merge_records([row('Q1','https://openalex.org/W1'),row('Q2','W1')],
                                  {'Q1':['M05'],'Q2':['M05']}, direct('W1'), {}, True)
        self.assertEqual(len(union),1)
        self.assertEqual(union[('M05','W1')]['artist_ids'],{'Q1','Q2'})
        self.assertEqual(union[('M05','W1')]['terms'],{'Impressionism'})

    def test_direct_only_and_artist_only_union(self):
        union,_,_ = m.merge_records([row('Q1','W1')],{'Q1':['M05']},direct('W2'),{},True)
        self.assertEqual(len(union),2)

    def test_multiple_movements_count_separately(self):
        union,_,_ = m.merge_records([row('Q1','W1')],{'Q1':['M05','M06']},{},{},False)
        self.assertEqual(set(union),{('M05','W1'),('M06','W1')})

    def test_review_required_unless_explicit(self):
        with self.assertRaisesRegex(ValueError,'Unreviewed'):
            m.merge_records([],{},direct('W1'),{},False)

    def test_direct_exclusion_preserves_artist_evidence(self):
        decisions={('M05','W1'):{'manual_label':'irrelevant','metadata_issue':'none','review_notes':'other sense'}}
        union,excluded,_ = m.merge_records([row('Q1','W1')],{'Q1':['M05']},direct('W1'),decisions,False)
        self.assertEqual(len(excluded),1)
        self.assertEqual(union[('M05','W1')]['terms'],set())

    def test_year_conflict_fails(self):
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            m.merge_records([row('Q1','W1')],{'Q1':['M05']},direct('W1',1901),{},True)

    def test_duplicate_artist_work_fails(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            m.merge_records([row('Q1','W1'),row('Q1','W1')],{'Q1':['M05']},{},{},False)

    def test_dictionary_and_terms(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);dictionary=p/'dict.csv';terms=p/'terms.csv'
            m.save_csv(dictionary,['artist_id','movement_ids','movement_names'],
                       [{'artist_id':'Q1','movement_ids':'M05; M06','movement_names':'Impressionism; Post-Impressionism'}])
            m.init(Namespace(dictionary=dictionary,terms=terms))
            artists,movements=m.dictionary(dictionary)
            self.assertEqual(len(m.terms(terms,movements)),2)
            self.assertEqual(artists['Q1'],['M05','M06'])

    def test_paginated_collection_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);dictionary=p/'dict.csv';terms=p/'terms.csv'
            m.save_csv(dictionary,['artist_id','movement_ids','movement_names'],
                       [{'artist_id':'Q1','movement_ids':'M05','movement_names':'Impressionism'}])
            m.init(Namespace(dictionary=dictionary,terms=terms))
            topic={'subfield':{'id':'https://openalex.org/subfields/1213'}}
            def work(n):return {'id':'https://openalex.org/W'+str(n),'title':'Impressionism',
                                'publication_year':1900,'language':'en','type':'article','topics':[topic],'primary_topic':topic}
            pages=[{'meta':{'count':2,'next_cursor':'next'},'results':[work(1)]},
                   {'meta':{'count':2,'next_cursor':None},'results':[work(2)]}]
            args=Namespace(dictionary=dictionary,terms=terms,collection=p/'collection',delay=0)
            with patch.dict(m.os.environ,{'OPENALEX_API_KEY':'test'}),patch.object(m.collector,'request_page',side_effect=pages) as request:
                m.collect(args);self.assertEqual(request.call_count,2)
                m.collect(args);self.assertEqual(request.call_count,2)
            _,movements=m.dictionary(dictionary)
            self.assertEqual(len(m.direct_rows(args,movements)),2)

    def test_denominator_reallocation_detected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);groups=[{'key':str(y),'count':2} for y in range(1800,2020)]
            raw=p/'annual_denominator_response.json'
            raw.write_text(json.dumps([{'meta':{'count':440,'next_cursor':None},'group_by':groups}]),encoding='utf-8')
            m.save_json(p/'denominator_manifest.json',{'filter':m.FILTER,'corpus':'core','types':['all'],
                       'scale':10000,'api_total':440,'raw_response_sha256':m.sha(raw)})
            rows=[{'year':y,'denominator_works':2} for y in range(1800,2020)]
            m.save_csv(p/'annual_denominator.csv',['year','denominator_works'],rows)
            self.assertEqual(m.denominator(p)[0][1800],2)
            rows[0]['denominator_works']=1;rows[1]['denominator_works']=3
            m.save_csv(p/'annual_denominator.csv',['year','denominator_works'],rows)
            with self.assertRaisesRegex(ValueError,'differs'):m.denominator(p)

if __name__ == '__main__': unittest.main()

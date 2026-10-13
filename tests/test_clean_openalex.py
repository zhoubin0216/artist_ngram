"""Offline self-tests for clean_openalex.py; no OpenAlex requests needed."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'clean_openalex.py'
spec = importlib.util.spec_from_file_location('clean_openalex', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_csv(path, headers, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        wr=csv.DictWriter(f,fieldnames=headers);wr.writeheader();wr.writerows(rows)


class CleaningTests(unittest.TestCase):
    def test_work_id_url_and_short_match(self):
        self.assertEqual(module.work_key('Q296','https://openalex.org/W123'), ('Q296','W123'))
        self.assertEqual(module.work_key('Q296','W123'), ('Q296','W123'))
        with self.assertRaises(ValueError):
            module.work_key('Q296', 'not an OpenAlex Work ID')

    def test_distinct_entity_and_metadata_actions(self):
        r={}
        self.assertEqual(module.decision_for(r,None)['cleaning_status'],'include')
        self.assertEqual(module.decision_for(r, {'entity_label':'irrelevant','metadata_issue':'none','review_source':'manual','review_notes':''})['cleaning_status'],'exclude')
        self.assertEqual(module.decision_for(r, {'entity_label':'uncertain','metadata_issue':'none','review_source':'manual','review_notes':''})['cleaning_status'],'exclude')
        self.assertEqual(module.decision_for(r, {'entity_label':'relevant','metadata_issue':'suspicious_year','review_source':'manual','review_notes':''})['cleaning_status'],'exclude')
        self.assertEqual(module.decision_for(r, {'entity_label':'relevant','metadata_issue':'none','review_source':'manual','review_notes':''})['cleaning_status'],'include')

    def test_conflicting_duplicate_human_labels_fail(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            make_csv(td/'reviews.csv',['artist_id','work_id','manual_label','metadata_issue'],
                     [{'artist_id':'Q1','work_id':'W42','manual_label':'relevant','metadata_issue':'none'}])
            make_csv(td/'issues.csv',['artist_id','work_id','entity_label','metadata_issue'],
                     [{'artist_id':'Q1','work_id':'https://openalex.org/W42','entity_label':'irrelevant','metadata_issue':'none'}])
            with self.assertRaisesRegex(ValueError,'Conflicting human decisions'):
                module.load_decisions(td/'reviews.csv',td/'issues.csv')

    def test_minimal_end_to_end_and_no_raw_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            inp=td/'input';out=td/'output'; (inp/'raw').mkdir(parents=True)
            make_csv(inp/'artist_collection_status.csv',['artist_id','artist_name','status','topic_scope','art_subfield_id','corpus'],[
                {'artist_id':'Q1','artist_name':'Alice','status':'complete','topic_scope':'any','art_subfield_id':'1213','corpus':'core'},
                {'artist_id':'Q2','artist_name':'Bob','status':'complete','topic_scope':'any','art_subfield_id':'1213','corpus':'core'}])
            raw=[{'artist_id':'Q1','artist_name':'Alice','work_id':f'https://openalex.org/W{n}',
                  'publication_year':2010,'language':'en','is_visual_arts':True,'topic_scope':'any',
                  'match_in_title':True,'match_in_abstract':False,'type':'article','title':f'Alice painting {n}',
                  'has_abstract':False,'primary_is_visual_arts':True,'risk_flags':[]} for n in (11,12,13,14)]
            data=''.join(json.dumps(x)+'\n' for x in raw)
            (inp/'raw/Q1.jsonl').write_text(data,encoding='utf-8')
            (inp/'raw/Q2.jsonl').write_text('',encoding='utf-8')
            make_csv(inp/'artist_year.csv',['artist_id','year','art_works_count'],[
                {'artist_id':'Q1','year':2010,'art_works_count':4}])
            make_csv(inp/'artist_year_type.csv',['artist_id','year','type','works_count'],[
                {'artist_id':'Q1','year':2010,'type':'article','works_count':4}])
            make_csv(td/'reviews.csv',['artist_id','work_id','manual_label','metadata_issue'],[
                {'artist_id':'Q1','work_id':'W12','manual_label':'irrelevant','metadata_issue':'none'},
                {'artist_id':'Q1','work_id':'W13','manual_label':'uncertain','metadata_issue':'none'}])
            make_csv(td/'issues.csv',['artist_id','work_id','entity_label','metadata_issue'],[
                {'artist_id':'Q1','work_id':'W14','entity_label':'relevant','metadata_issue':'suspicious_year'}])
            result=module.run(inp,td/'reviews.csv',td/'issues.csv',out,expected_artists=2)
            self.assertEqual(result['statuses'],{'include':1,'exclude':3})
            self.assertEqual(result['dense_year_rows'],440)
            self.assertEqual(result['confirmed_wrong_entity_exclusions'],1)
            self.assertEqual(result['precautionary_exclusions'],2)
            with (out/'excluded_from_analysis.csv').open(encoding='utf-8-sig') as fr:
                exclusion_rows=list(csv.DictReader(fr))
            self.assertEqual({r['exclusion_basis'] for r in exclusion_rows}, {'confirmed_wrong_entity','precautionary'})
            self.assertEqual((inp/'raw/Q1.jsonl').read_text(encoding='utf-8'),data)
            with (out/'artist_year_clean.csv').open(encoding='utf-8-sig') as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),1)
            self.assertEqual(int(rows[0]['works_count']),1)
            with (out/'artist_year_dense_clean.csv').open(encoding='utf-8-sig') as f:
                dense=list(csv.DictReader(f))
            self.assertEqual(len(dense),440)
            self.assertEqual(len([r for r in dense if r['artist_id']=='Q2' and r['works_count']=='0']),220)
            self.assertEqual(module.run(inp,td/'reviews.csv',td/'issues.csv',out,2)['cleaned_matched_records'],1)

if __name__ == '__main__':
    unittest.main()

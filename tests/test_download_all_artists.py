import base64
import copy
import gzip
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import download_all_artists as full


class FullTests(unittest.TestCase):
    def fixture(self, root):
        raw = root / 'raw'; raw.mkdir()
        shards = []
        for i, (low, high, text) in enumerate([
            ('A', 'M', 'Claude Monet\t1900,8,3\nBert Person\t1900,5,2\n'),
            ('M', None, 'Zed Painter\t1900,4,2\n')]):
            data = gzip.compress(text.encode()); filename = f'2-0000{i}-of-00002.gz'
            meta = {'file': filename, 'url': full.source.BASE+'/'+filename, 'n':2,
                    'size':len(data), 'etag':'"test"', 'md5':base64.b64encode(hashlib.md5(data).digest()).decode(),
                    'lower':low, 'upper':high}
            (raw/filename).write_bytes(data); shards.append(meta)
        total = b'1900,100,20,10\n'
        (raw/'totalcounts-2').write_bytes(total)
        tm = {'file':'totalcounts-2','url':full.source.BASE+'/totalcounts-2','size':len(total),
              'etag':'"total"','md5':base64.b64encode(hashlib.md5(total).digest()).decode()}
        artists = [{'qid':'Q1','query':'Claude Monet','n':2,'shard':shards[0]['file']},
                   {'qid':'Q2','query':'Absent Artist','n':2,'shard':shards[0]['file']},
                   {'qid':'Q3','query':'Zed Painter','n':2,'shard':shards[1]['file']}]
        m = {'schema':1,'corpus':'eng','version':'20200217','start_year':1800,'end_year':2019,
             'shards':shards,'totals':{'2':tm},'artists':artists,'matching':'exact','source':'synthetic','license':'test'}
        pilot_manifest=root/'pilot_manifest.json'; full.source.save_json(pilot_manifest,m)
        pilot=root/'pilot'
        full.counts.run(pilot_manifest,pilot,raw,1)
        m=copy.deepcopy(m)
        m['artists'].append({'qid':'Q4','query':'Bert Person','n':2,'shard':shards[0]['file']})
        path=root/'full_manifest.json'; full.source.save_json(path,m)
        return m,path,root/'output',raw,pilot

    def test_seed_reuse_requires_complete_query_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,_,out,_,pilot=self.fixture(Path(tmp))
            seeds=full.load_seeds(m,pilot)
            completed=full.prepare_checkpoints(m,out,seeds)
            self.assertEqual(len(full.collect(seeds,{})[1]),3)
            self.assertEqual(set(completed),{m['shards'][1]['file']})
            self.assertFalse(full.counts.checkpoint_path(out,m['shards'][0]).exists())

    def test_parallel_completion_and_offline_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,path,out,raw,pilot=self.fixture(Path(tmp))
            with patch.object(full.source,'request',side_effect=AssertionError('no network')):
                r=full.run(path,out,raw,pilot,2,1)
                self.assertEqual(r['run_status'],'complete')
                self.assertEqual(r['status_counts'],{'matched':3,'not_found':1})
                data=(out/'artist_year_counts.jsonl').read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(),r['data_sha256'])
                with patch.object(full.counts,'extract_one',side_effect=AssertionError('no rescan')):
                    full.run(path,out,raw,pilot,2,1)
                self.assertEqual(data,(out/'artist_year_counts.jsonl').read_bytes())

    def test_failed_shard_is_not_missing_or_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,path,out,raw,pilot=self.fixture(Path(tmp))
            with patch.object(full.counts,'extract_one',side_effect=OSError('synthetic network failure')), patch.object(full.time,'sleep'):
                r=full.run(path,out,raw,pilot,2,1)
            self.assertEqual(r['run_status'],'incomplete')
            self.assertEqual(r['status_counts'],{'matched':2,'not_found':1,'not_downloaded':1})
            self.assertEqual(len(r['failed_shards']),1)

    def test_fresh_run_without_pilot(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,path,out,raw,_=self.fixture(Path(tmp))
            with patch.object(full.source,'request',side_effect=AssertionError('no network')):
                r=full.run(path,out,raw,workers=2,max_gib=1)
            self.assertEqual(r['run_status'],'complete')
            self.assertEqual(r['status_counts'],{'matched':3,'not_found':1})
            self.assertEqual(r['pilot_queries_verified'],0)

    def test_cli_resumes_published_manifest_without_pilot_or_local_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,path,out,raw,pilot=self.fixture(Path(tmp))
            full.run(path,out,raw,pilot,2,1)
            before=(out/'artist_year_counts.jsonl').read_bytes()
            # No --pilot or --manifest: the committed output is sufficient.
            with patch.object(full.source,'ROOT',Path(tmp)), \
                 patch.object(full.source,'request',side_effect=AssertionError('no network')), \
                 patch.object(full.counts,'extract_one',side_effect=AssertionError('no rescan')), \
                 patch.object(sys,'argv',['download_all_artists.py','--output',str(out),'--max-gib','1']):
                self.assertEqual(full.main(),0)
            self.assertEqual(before,(out/'artist_year_counts.jsonl').read_bytes())

    def test_explicit_missing_pilot_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,path,out,raw,_=self.fixture(Path(tmp))
            with self.assertRaises(FileNotFoundError):
                full.run(path,out,raw,Path(tmp)/'missing-pilot',2,1)

    def test_process_executor_real_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,path,out,raw,pilot=self.fixture(Path(tmp))
            r=full.run(path,out,raw,pilot,2,1,use_processes=True)
            self.assertEqual(r['run_status'],'complete')
            self.assertEqual(r['status_counts'],{'matched':3,'not_found':1})

    def test_resume_disk_budget_does_not_double_count_staged_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,path,out,raw,pilot=self.fixture(Path(tmp))
            scratch=out/'.downloads'; scratch.mkdir(parents=True)
            meta=m['shards'][0]
            (scratch/meta['file']).write_bytes((raw/meta['file']).read_bytes())
            with patch.object(full.shutil,'disk_usage',return_value=SimpleNamespace(free=1024**3)):
                r=full.run(path,out,raw/'missing',pilot,2,1)
            self.assertEqual(r['run_status'],'complete')
            self.assertFalse(list(scratch.iterdir()))

    def test_seed_identity_and_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,_,_,_,pilot=self.fixture(Path(tmp))
            changed=copy.deepcopy(m); changed['shards'][0]['etag']='"changed"'
            with self.assertRaisesRegex(ValueError,'source object changed'):
                full.load_seeds(changed,pilot)
            with self.assertRaisesRegex(ValueError,'disagrees'):
                full.verify_overlap({'queries':['X'],'found':{}},{'queries':['X'],'found':{'X':{'1900':[1,1]}}})

    def test_budget_workers_and_duplicate_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,path,out,raw,pilot=self.fixture(Path(tmp))
            with patch.object(full.source,'request') as request:
                with self.assertRaisesRegex(ValueError,'budget'):
                    full.run(path,out,raw/'missing',pilot,2,1e-12)
                with self.assertRaisesRegex(ValueError,'workers'):
                    full.run(path,out,raw,pilot,9,1)
                request.assert_not_called()
            m['artists'].append(m['artists'][0]); full.source.save_json(path,m)
            with self.assertRaisesRegex(ValueError,'Duplicate artist'):
                full.validate_manifest(path)

    def test_crash_cleanup_only_verified_owned_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,_,out,raw,pilot=self.fixture(Path(tmp))
            seeds=full.load_seeds(m,pilot); full.prepare_checkpoints(m,out,seeds)
            scratch=out/'.downloads'; scratch.mkdir()
            meta=m['shards'][1]; owned=scratch/meta['file']
            owned.write_bytes((raw/meta['file']).read_bytes())
            full.prepare_checkpoints(m,out,seeds)
            self.assertFalse(owned.exists())
            self.assertTrue((raw/meta['file']).exists())


if __name__=='__main__':
    unittest.main()

"""Offline tests: all server bytes below are synthetic, not scientific results."""
import base64
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("ngram_sample", Path(__file__).resolve().parents[1] / "scripts/ngram_sample.py")
ngram = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ngram)


class ParsingTests(unittest.TestCase):
    def test_v3_sparse_row(self):
        name, values = ngram.parse_record("Claude Monet\t1840,3,2\t2000,8,5\n")
        self.assertEqual(name, "Claude Monet")
        self.assertEqual(values, {1840: (3, 2), 2000: (8, 5)})

    def test_bad_records(self):
        for line in ("X\t1840\t1\t1", "X\t1840,1,2", "X\t1840,1,1\t1840,2,1", "X\t1840,-1,0"):
            with self.subTest(line=line), self.assertRaises(ValueError):
                ngram.parse_record(line)

    def test_totals_and_duplicate(self):
        self.assertEqual(ngram.parse_totals(" \t1800,100,10,2\t1801,200,20,3\t"), {1800: 100, 1801: 200})
        for text in ("1800,0,1,1", "1800,100,1,1\t1800,200,2,1", "1800,100,1"):
            with self.assertRaises(ValueError):
                ngram.parse_totals(text)

    def test_boundary_inclusive_and_unicode(self):
        links = [("A A", "one"), ("Claude Monet", "two"), ("Édouard Manet", "three")]
        self.assertEqual(ngram.locate(links, "Claude Monet")["url"], "two")
        self.assertEqual(ngram.locate(links, "Clara Peeters")["url"], "one")
        self.assertEqual(ngram.locate(links, "Édouard Manet")["url"], "three")

    def test_html_unescape_and_reject_unsorted(self):
        source = ''.join(f'<a href="{ngram.BASE}/2-0000{i}-of-00002.gz">{label}</a>'
                         for i, label in enumerate(["A &amp;", "B C"]))
        self.assertEqual(ngram.parse_index(source, 2)[0][0], "A &")
        with self.assertRaises(ValueError):
            ngram.parse_index(source.replace("A &amp;", "Z Z"), 2)
        with self.assertRaises(ValueError):
            ngram.parse_index(source.replace(ngram.BASE, "https://example.com"), 2)

    def test_unsorted_rows_exact_match_and_full_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.gz"
            with gzip.open(path, "wt") as output:
                output.write("Z Z\t1900,1,1\nClaude Monet\t1900,2,1\nclaude Monet\t1900,5,1\nA A\t1900,1,1\n")
            found, lines = ngram.scan_shard(path, {"lower": "A A", "upper": None}, {"Claude Monet"})
            self.assertEqual(lines, 4)
            self.assertEqual(found, {"Claude Monet": {1900: (2, 1)}})
            with self.assertRaises(ValueError):
                ngram.scan_shard(path, {"lower": "B B", "upper": None}, {"Claude Monet"})

    def test_truncated_gzip_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "truncated.gz"
            path.write_bytes(gzip.compress(b"Claude Monet\t1900,2,1\n")[:-6])
            with self.assertRaises(EOFError):
                ngram.scan_shard(path, {"lower": "A", "upper": None}, {"Claude Monet"})

    def test_series_zero_fill_only_for_matched_name(self):
        artist = {"qid": "Q296", "query": "Claude Monet", "n": 2, "birth_year": 1840}
        rows, summary = ngram.make_series(artist, {1839: (2, 1), 1841: (8, 3)},
                                         {1839: 100, 1840: 200, 1841: 400}, 1839, 1841)
        self.assertEqual([r["match_count"] for r in rows], [2, 0, 8])
        self.assertEqual(rows[2]["frequency"], .02)
        self.assertEqual(rows[2]["per_billion"], 20_000_000)
        self.assertEqual(summary["prebirth_match_share"], .2)
        self.assertTrue(summary["manual_review"])
        with self.assertRaises(ValueError):
            ngram.make_series(artist, {}, {}, 1839, 1841)

    def test_unsupported_year_and_invalid_budget(self):
        self.assertEqual(ngram.main(["plan", "--end-year", "2022"]), 1)
        self.assertEqual(ngram.main(["download", "--max-gib", "nan"]), 1)

    def test_duplicate_ngram_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.gz"
            path.write_bytes(gzip.compress(b"Claude Monet\t1900,2,1\nClaude Monet\t1901,3,2\n"))
            with self.assertRaisesRegex(ValueError, "Duplicate ngram"):
                ngram.scan_shard(path, {"lower": "A", "upper": None}, {"Claude Monet"})

    def test_matched_outside_window(self):
        artist = {"qid": "Q1", "query": "Test Artist", "n": 2, "birth_year": 1800}
        rows, summary = ngram.make_series(artist, {1850: (1, 1)}, {1900: 1000}, 1900, 1900)
        self.assertEqual(summary["status"], "matched_outside_window")
        self.assertTrue(summary["manual_review"])
        self.assertEqual(rows[0]["match_count"], 0)

    def test_sample_deterministic_and_unknown_qid(self):
        artists = [{"qid": "Q296", "query": "Claude Monet", "movements": ["impressionism"]},
                   {"qid": "Q1", "query": "Example Artist", "movements": ["cubism"]}]
        self.assertEqual(ngram.choose_sample(artists, 2), artists)
        self.assertEqual(ngram.choose_sample(artists, 25, "Q1,Q1"), [artists[1]])
        with self.assertRaises(ValueError):
            ngram.choose_sample(artists, 1, "Q404")


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = b"synthetic test data\n" * 4000
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append(dict(self.headers))
                if self.headers.get("If-Match") != '"fixture-v1"':
                    self.send_error(412)
                    return
                start = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
                if self.path == "/ignore":
                    start = 0
                self.send_response(206 if start else 200)
                self.send_header("ETag", '"fixture-v1"')
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{len(cls.payload)-1}/{len(cls.payload)}")
                self.send_header("Content-Length", str(len(cls.payload) - start))
                self.end_headers()
                self.wfile.write(cls.payload[start:])

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def metadata(self, route="data"):
        return {"file": "data.gz", "url": f"http://127.0.0.1:{self.server.server_port}/{route}",
                "size": len(self.payload), "etag": '"fixture-v1"',
                "md5": base64.b64encode(hashlib.md5(self.payload).digest()).decode()}

    def test_download_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.metadata()
            sha = ngram.download(meta, Path(tmp), attempts=1)
            self.assertEqual(sha, hashlib.sha256(self.payload).hexdigest())
            count = len(self.requests)
            self.assertEqual(ngram.download(meta, Path(tmp), attempts=1), sha)
            self.assertEqual(len(self.requests), count)

    def test_resume_and_server_ignoring_range(self):
        for route in ("data", "ignore"):
            with self.subTest(route=route), tempfile.TemporaryDirectory() as tmp:
                directory, meta = Path(tmp), self.metadata(route)
                (directory / "data.gz.part").write_bytes(self.payload[:111])
                ngram.save_json(directory / "data.gz.part.json", {k: meta[k] for k in ("url", "size", "etag", "md5")})
                ngram.download(meta, directory, attempts=1)
                self.assertEqual((directory / "data.gz").read_bytes(), self.payload)
                self.assertEqual(self.requests[-1]["Range"], "bytes=111-")

    def test_complete_partial_finalizes_without_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, meta = Path(tmp), self.metadata()
            (directory / "data.gz.part").write_bytes(self.payload)
            ngram.save_json(directory / "data.gz.part.json", {k: meta[k] for k in ("url", "size", "etag", "md5")})
            count = len(self.requests)
            ngram.download(meta, directory, attempts=1)
            self.assertEqual(len(self.requests), count)

    def test_corrupt_cache_and_foreign_partial_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, meta = Path(tmp), self.metadata()
            path = directory / "data.gz"
            path.write_bytes(b"X" * len(self.payload))
            with self.assertRaisesRegex(ValueError, "MD5"):
                ngram.download(meta, directory, attempts=1)
            path.rename(directory / "data.gz.part")
            with self.assertRaisesRegex(ValueError, "another download"):
                ngram.download(meta, directory, attempts=1)

    def test_remote_changed_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = {**self.metadata(), "etag": '"stale"'}
            with self.assertRaises(ngram.HTTPError) as context:
                ngram.download(meta, Path(tmp), attempts=1)
            self.assertEqual(context.exception.code, 412)


class WorkflowTests(unittest.TestCase):
    def make_fixture(self, root):
        raw = root / "data/ngram/raw"
        raw.mkdir(parents=True)
        contents = {"2-00000-of-00001.gz": gzip.compress(b"Claude Monet\t1840,20,3\n"),
                    "totalcounts-2": b"1839,100,10,1\t1840,200,20,2"}
        metas = {}
        for filename, data in contents.items():
            (raw / filename).write_bytes(data)
            metas[filename] = {"file": filename, "url": ngram.BASE + "/" + filename,
                               "size": len(data), "etag": '"fixture"',
                               "md5": base64.b64encode(hashlib.md5(data).digest()).decode()}
        shard = {**metas["2-00000-of-00001.gz"], "n": 2, "lower": "A", "upper": None}
        artist = {"qid": "Q296", "query": "Claude Monet", "n": 2, "birth_year": 1840,
                  "status": "planned", "shard": shard["file"]}
        manifest = {"schema": 1, "version": "20200217", "corpus": "eng", "start_year": 1839,
                    "end_year": 1840, "artists": [artist, {**artist, "qid": "Q0", "query": "Not Present"}],
                    "shards": [shard], "totals": {"2": metas["totalcounts-2"]}}
        path = root / "manifest.json"
        ngram.save_json(path, manifest)
        return path, manifest

    def test_end_to_end_missing_not_zero_and_strict_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, _ = self.make_fixture(root)
            self.assertEqual(ngram.main(["validate", "--root", tmp, "--manifest", str(path), "--require-all"]), 2)
            result = ngram.read_json(root / "outputs/ngram_sample/report.json")
            self.assertEqual(result["status_counts"], {"matched": 1, "not_found": 1})
            rows = [json.loads(line) for line in (root / "outputs/ngram_sample/annual.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["per_billion"], 100_000_000)

    def test_undownloaded_and_budget_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, manifest = self.make_fixture(root)
            (root / "data/ngram/raw" / manifest["shards"][0]["file"]).unlink()
            self.assertEqual(ngram.main(["validate", "--root", tmp, "--manifest", str(path), "--require-all"]), 2)
            report = ngram.read_json(root / "outputs/ngram_sample/report.json")
            self.assertEqual(report["status_counts"], {"not_downloaded": 2})
            with patch.object(ngram, "request", side_effect=AssertionError("Must not access network")):
                self.assertEqual(ngram.main(["download", "--root", tmp, "--manifest", str(path), "--max-gib", "0.000000001"]), 1)

    def test_unsafe_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, manifest = self.make_fixture(Path(tmp))
            manifest["shards"][0]["file"] = "../../outside"
            ngram.save_json(path, manifest)
            with self.assertRaises(ValueError):
                ngram.load_manifest(path)


if __name__ == "__main__":
    unittest.main()

"""Offline tests; synthetic bytes only."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import download_artist_counts as counts


class CountTests(unittest.TestCase):
    def fixture(self, root):
        raw = gzip.compress(b"Claude Monet\t999,1,1\t1900,8,3\t2020,1,1\nOther Artist\t1900,1,1\n")
        meta = {"file": "2-00000-of-00001.gz", "url": counts.source.BASE + "/2-00000-of-00001.gz",
                "size": len(raw), "etag": '"test"', "md5": base64.b64encode(hashlib.md5(raw).digest()).decode(),
                "lower": "A", "upper": "Z", "n": 2}
        output, existing = root / "out", root / "existing"
        output.mkdir(); existing.mkdir()
        return raw, meta, output, existing

    def test_existing_is_preserved_and_resume_needs_no_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, meta, out, existing = self.fixture(Path(tmp))
            path = existing / meta["file"]
            path.write_bytes(raw)
            result = counts.extract_one(meta, {"Claude Monet", "Absent Artist"}, out, existing)
            self.assertEqual(result["found"]["Claude Monet"]["1900"], [8, 3])
            self.assertNotIn("Absent Artist", result["found"])
            self.assertEqual(path.read_bytes(), raw)
            with patch.object(counts.source, "scan_shard", side_effect=AssertionError("must not scan")):
                self.assertEqual(counts.extract_one(meta, {"Claude Monet", "Absent Artist"}, out, existing), result)

    def test_success_removes_only_owned_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, meta, out, existing = self.fixture(Path(tmp))
            scratch = out / ".downloads"
            scratch.mkdir()
            (scratch / meta["file"]).write_bytes(raw)
            unrelated = scratch / "keep.txt"
            unrelated.write_text("keep")
            counts.extract_one(meta, {"Claude Monet"}, out, existing)
            self.assertFalse((scratch / meta["file"]).exists())
            self.assertTrue(unrelated.exists())

    def test_failed_scan_preserves_owned_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, meta, out, existing = self.fixture(Path(tmp))
            scratch = out / ".downloads"
            scratch.mkdir()
            path = scratch / meta["file"]
            path.write_bytes(raw)
            with patch.object(counts.source, "scan_shard", side_effect=ValueError("bad scan")):
                with self.assertRaises(ValueError):
                    counts.extract_one(meta, {"Claude Monet"}, out, existing)
            self.assertTrue(path.exists())
            self.assertFalse(counts.checkpoint_path(out, meta).exists())

    def test_checkpoint_corruption_and_wrong_selection_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, meta, out, existing = self.fixture(Path(tmp))
            (existing / meta["file"]).write_bytes(raw)
            counts.extract_one(meta, {"Claude Monet"}, out, existing)
            path = counts.checkpoint_path(out, meta)
            with self.assertRaisesRegex(ValueError, "selection"):
                counts.read_checkpoint(path, meta, {"Other Artist"})
            saved = counts.source.read_json(path)
            saved["payload"]["found"]["Claude Monet"]["1900"] = [999, 3]
            counts.source.save_json(path, saved)
            with self.assertRaisesRegex(ValueError, "checksum"):
                counts.read_checkpoint(path, meta, {"Claude Monet"})

    def test_sparse_raw_rows_no_fabricated_zeros(self):
        artists = [{"qid": "Q296", "query": "Claude Monet", "n": 2, "shard": "s"},
                   {"qid": "Q1", "query": "Absent Artist", "n": 2, "shard": "s"}]
        manifest = {"artists": artists, "start_year": 1800, "end_year": 2019}
        rows, status = counts.build_rows(manifest, {"Claude Monet": {"1799": [1, 1], "1900": [8, 3], "2020": [2, 1]}}, {"2": {1900: 100}})
        self.assertEqual(rows, [{"artist_id": "Q296", "query": "Claude Monet", "n": 2,
                                 "year": 1900, "match_count": 8, "volume_count": 3}])
        self.assertEqual([a["status"] for a in status], ["matched", "not_found"])
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            counts.build_rows(manifest, {"Claude Monet": {"1900": [101, 3]}}, {"2": {1900: 100}})

    def test_insufficient_disk_prevents_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, meta, out, existing = self.fixture(Path(tmp))
            with patch.object(counts.shutil, "disk_usage") as disk, patch.object(counts.source, "download") as download:
                disk.return_value.free = 1
                with self.assertRaisesRegex(ValueError, "free space"):
                    counts.extract_one(meta, {"Claude Monet"}, out, existing)
                download.assert_not_called()

    def test_complete_workflow_and_offline_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw, meta, out, existing = self.fixture(root)
            (existing / meta["file"]).write_bytes(raw)
            total_raw = b"1799,50,10,2\t1900,100,20,5\t2020,200,30,8\n"
            total = {"file": "totalcounts-2", "url": counts.source.BASE + "/totalcounts-2",
                     "size": len(total_raw), "etag": '"total"',
                     "md5": base64.b64encode(hashlib.md5(total_raw).digest()).decode()}
            (existing / total["file"]).write_bytes(total_raw)
            manifest = {"schema": 1, "corpus": "eng", "version": "20200217", "start_year": 1800,
                        "end_year": 2019, "matching": "exact", "source": "synthetic test", "license": "test",
                        "shards": [meta], "totals": {"2": total}, "artists": [
                            {"qid": "Q296", "query": "Claude Monet", "n": 2, "shard": meta["file"]},
                            {"qid": "Q1", "query": "Absent Artist", "n": 2, "shard": meta["file"]}]}
            path = root / "manifest.json"
            counts.source.save_json(path, manifest)
            with patch.object(counts.source, "request", side_effect=AssertionError("offline test")):
                report = counts.run(path, out, existing, 1)
                self.assertEqual(report["status_counts"], {"matched": 1, "not_found": 1})
                self.assertEqual(report["row_count"], 1)
                data = (out / "artist_year_counts.jsonl").read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), report["data_sha256"])
                self.assertEqual((out / "totalcounts" / "totalcounts-2").read_bytes(), total_raw)
                with patch.object(counts.source, "scan_shard", side_effect=AssertionError("resume")):
                    counts.run(path, out, existing, 1)
                self.assertEqual((out / "artist_year_counts.jsonl").read_bytes(), data)
                for budget in (0, float("nan"), float("inf")):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        counts.run(path, out, existing, budget)

    def test_budget_blocks_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, meta, out, existing = self.fixture(root)
            manifest = {"schema": 1, "corpus": "eng", "version": "20200217", "start_year": 1800,
                        "end_year": 2019, "shards": [meta], "totals": {},
                        "artists": [{"query": "Claude Monet", "shard": meta["file"]}]}
            path = root / "manifest.json"
            counts.source.save_json(path, manifest)
            with patch.object(counts.source, "request") as request:
                with self.assertRaisesRegex(ValueError, "exceeds budget"):
                    counts.run(path, out, existing, 1e-12)
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()

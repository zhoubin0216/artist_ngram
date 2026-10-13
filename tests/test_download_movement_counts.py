"""Offline movement tests use synthetic Ngram bytes, never empirical observations."""
import base64
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import download_movement_counts as movement


class MovementTests(unittest.TestCase):
    def fixture(self, root):
        existing = root / "raw"
        existing.mkdir()
        items, totals, rows = [], {}, []
        for n, query, requested, mid, text in [
            (1, "Realism", "Realism", "M03", "Realism\t1799,2,1\t1900,8,3\t2020,1,1\nZebra\t1900,1,1\n"),
            (3, "Post - Impressionism", "Post-Impressionism", "M06", "Post - Impressionism\t1900,5,2\n")]:
            raw = gzip.compress(text.encode())
            name = f"{n}-00000-of-00001.gz"
            meta = {"file": name, "url": movement.source.BASE + "/" + name, "size": len(raw),
                    "etag": '"test"', "md5": base64.b64encode(hashlib.md5(raw).digest()).decode(),
                    "n": n, "lower": "A", "upper": "z"}
            (existing / name).write_bytes(raw)
            items.append(meta)
            tb = b"1799,50,10,2\t1900,100,20,10\t2020,200,30,20\n"
            tn = f"totalcounts-{n}"
            totals[str(n)] = {"file": tn, "url": movement.source.BASE + "/" + tn, "size": len(tb),
                               "etag": '"total"', "md5": base64.b64encode(hashlib.md5(tb).digest()).decode()}
            (existing / tn).write_bytes(tb)
            rows.append({"movement_id": mid, "query": query, "requested_query": requested,
                         "n": n, "shard": name, "status": "planned"})
        rows.append({"movement_id": "M13", "query": "Dadaism", "requested_query": "Dadaism", "n": 1,
                     "shard": items[0]["file"], "status": "planned"})
        m = {"schema": 1, "entity_type": "movement", "corpus": "eng", "version": "20200217",
             "start_year": 1800, "end_year": 2019, "shards": items, "totals": totals, "movements": rows,
             "matching": "exact", "source": "synthetic", "license": "test"}
        path = root / "manifest.json"
        movement.source.save_json(path, m)
        return m, path, root / "output", existing

    def test_reviewed_selection(self):
        self.assertEqual([r[0] for r in movement.PRIMARY], [f"M{x:02}" for x in range(1, 16)])
        self.assertEqual(movement.PRIMARY[5][3], "Post-Impressionism")
        self.assertEqual(movement.PRIMARY[12][3], "Dadaism")

    def test_viewer_normalization_metadata_only(self):
        response = [{"ngram": "Post - Impressionism", "type": "NGRAM", "parent": "", "timeseries": [.1, .2]}]
        with patch.object(movement.source, "request", return_value=io.BytesIO(json.dumps(response).encode())):
            query, evidence = movement.normalize_query("Post-Impressionism")
        self.assertEqual(query, "Post - Impressionism")
        self.assertEqual(len(query.split()), 3)
        self.assertNotIn("timeseries", evidence)
        self.assertIn("corpus=26", evidence["url"])

    def test_normalization_refuses_absent_or_changed_query(self):
        for result in ([], [{"ngram": "realism", "type": "NGRAM", "parent": ""}],
                       [{"ngram": "Realism", "type": "EXPANSION", "parent": ""}]):
            with self.subTest(result=result), patch.object(movement.source, "request", return_value=io.BytesIO(json.dumps(result).encode())):
                with self.assertRaises(ValueError):
                    movement.normalize_query("Realism")

    def test_onegram_index(self):
        text = f'<a href="{movement.source.BASE}/1-00000-of-00001.gz">A</a>'
        self.assertEqual(movement.source.parse_index(text, 1)[0][0], "A")

    def test_manifest_rejects_unsafe_duplicate_and_wrong_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, path, _, _ = self.fixture(Path(tmp))
            self.assertEqual(movement.load_manifest(path)["shards"][0]["n"], 1)
            bad = copy.deepcopy(m); bad["shards"][0]["file"] = "../raw.gz"
            duplicate = copy.deepcopy(m); duplicate["movements"][1]["movement_id"] = "M03"
            wrong = copy.deepcopy(m); wrong["movements"][0]["n"] = 2
            for item in (bad, duplicate, wrong):
                movement.source.save_json(path, item)
                with self.assertRaises(ValueError):
                    movement.load_manifest(path)

    def test_sparse_counts_and_missing_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, _, _, _ = self.fixture(Path(tmp))
            found = {(1, "Realism"): {"1799": [1, 1], "1900": [8, 3], "2020": [2, 1]}}
            rows, metadata = movement.build_rows(m, found, {"1": {1900: 100}, "3": {1900: 100}})
            self.assertEqual(rows, [{"movement_id": "M03", "query": "Realism", "n": 1,
                                    "year": 1900, "match_count": 8, "volume_count": 3}])
            self.assertEqual([r["status"] for r in metadata], ["matched", "not_found", "not_found"])
            with self.assertRaisesRegex(ValueError, "inconsistent"):
                movement.build_rows(m, found, {"1": {1900: 5}})
            with self.assertRaisesRegex(ValueError, "denominator"):
                movement.build_rows(m, found, {"1": {}})

    def test_end_to_end_and_offline_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, path, out, existing = self.fixture(Path(tmp))
            with patch.object(movement.source, "request", side_effect=AssertionError("offline")):
                report = movement.run(path, out, existing, 1)
                self.assertEqual(report["status_counts"], {"matched": 2, "not_found": 1})
                self.assertEqual(report["row_count"], 2)
                data = (out / "movement_year_counts.jsonl").read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), report["data_sha256"])
                with patch.object(movement.source, "scan_shard", side_effect=AssertionError("no rescan")):
                    movement.run(path, out, existing, 1e-9)
                self.assertEqual((out / "movement_year_counts.jsonl").read_bytes(), data)
                self.assertTrue(all((existing / x["file"]).exists() for x in m["shards"]))
                self.assertEqual((out / "totalcounts/totalcounts-1").read_bytes(), (existing / "totalcounts-1").read_bytes())

    def test_budget_refused_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, path, out, existing = self.fixture(Path(tmp))
            missing_cache = existing / "not_cached"
            with patch.object(movement.source, "request") as request:
                with self.assertRaisesRegex(ValueError, "budget"):
                    movement.run(path, out, missing_cache, 1e-12)
                for amount in (0, float('nan'), float('inf')):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        movement.run(path, out, existing, amount)
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()

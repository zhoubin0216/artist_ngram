"""Offline tests for Stage B: no network, no original data modification."""
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "scripts" / "ml" / "validate_ngram_with_openalex.py"
spec = importlib.util.spec_from_file_location("stage_b", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class StageBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.a3 = self.root / "a3"
        self.a3.mkdir()
        self.oa = self.root / "openalex.csv"
        self.output = self.root / "out"
        self.ids = ["Q1", "Q2", "Q3", "Q4"]
        self.deaths = [1880, 1890, 1910, 1940]
        assn, feat, oa = [], [], []
        for i, (aid, death) in enumerate(zip(self.ids, self.deaths)):
            assn.append({"artist_id": aid, "death_cluster_id": i // 2})
            f = {"artist_id": aid, "canonical_name": f"Artist {i}", "death_year": death,
                 "eligible": True, "identity_warning": i==0}
            for b in m.BINS:
                f[f"rate_rel_{b}"] = (b + 31) + 3 * i if i < 2 else 100 - b + i
            feat.append(f)
            for year in range(1800, 2020):
                works = 0
                if i < 3 and (year - death) in (-20, -10, -1, 3, 12, 22, 32, 42):
                    works = (1 if i < 2 else 2)
                oa.append({"artist_id": aid, "year": year, "works_count": works,
                           "denominator_works": 100 + (year - 1800),
                           "analysis_works_count": works,
                           "is_complete": 1, "normalization_status": "ok"})
        pd.DataFrame(assn).to_csv(self.a3 / "death_aligned_assignments.csv", index=False)
        pd.DataFrame(feat).to_csv(self.a3 / "death_aligned_features.csv", index=False)
        pd.DataFrame(oa).to_csv(self.oa, index=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_inputs_and_aligned_bins(self):
        assn, oa = m.read_inputs(self.a3, self.oa)
        rows = m.construct_relative_rows(assn, oa)
        self.assertEqual(len(rows), 32)
        self.assertTrue((rows.years_observed == 10).all())
        r = rows[(rows.artist_id=='Q1') & (rows.relative_bin_start==-20)].iloc[0]
        self.assertEqual(int(r.works_count), 1)
        self.assertEqual(int(r.denominator_works), sum(100+(year-1800) for year in range(1860, 1870)))
        self.assertAlmostEqual(r.works_per_10000, 10000 / r.denominator_works)

    def test_not_impute_missing_years(self):
        oa = pd.read_csv(self.oa)
        oa = oa[~((oa.artist_id=='Q1') & (oa.year==1880))]
        oa.to_csv(self.oa, index=False)
        with self.assertRaisesRegex(ValueError, "incomplete|rows, expected"):
            m.read_inputs(self.a3, self.oa)

    def test_duplicate_artist_year_rejected(self):
        oa = pd.read_csv(self.oa)
        oa = pd.concat([oa, oa.iloc[[1]]], ignore_index=True)
        oa.to_csv(self.oa, index=False)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            m.read_inputs(self.a3, self.oa)

    def test_signal_filter_keeps_silent_artist_in_coverage(self):
        assn, oa = m.read_inputs(self.a3, self.oa)
        rows = m.construct_relative_rows(assn, oa)
        per = m.per_artist_summary(assn, rows, 3, 2)
        self.assertEqual(len(per), 4)
        self.assertFalse(bool(per.loc[per.artist_id=='Q4', 'openalex_signal_sufficient'].iloc[0]))
        self.assertEqual(int(per.loc[per.artist_id=='Q4', 'openalex_window_works'].iloc[0]), 0)
        self.assertEqual(int(per.openalex_signal_sufficient.sum()), 3)

    def test_full_run_and_no_overwrite(self):
        one, summary = m.execute(self.a3, self.oa, self.output)
        self.assertEqual(summary['artist_count'], 4)
        self.assertEqual(summary['openalex_artist_with_any_works'], 3)
        self.assertEqual(summary['openalex_artist_with_signal'], 3)
        self.assertTrue((one/'figures'/'ngram_openalex_shapes.png').exists())
        self.assertTrue((one/'REPORT_STAGE_B.md').exists())
        two, summary2 = m.execute(self.a3, self.oa, self.output)
        self.assertNotEqual(one, two)
        self.assertTrue((one/'comparison_summary.json').exists())
        self.assertEqual(summary, summary2)


if __name__ == '__main__':
    unittest.main()

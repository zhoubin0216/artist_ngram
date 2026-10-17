#!/usr/bin/env python3
"""Stage B: independent OpenAlex comparison for Stage A3 Ngram clusters.

No model is fitted. No API calls. All pre-existing inputs are read-only.
The output directory is new on every run; existing A1/A2/A3 files are unchanged.

Run at artist_ngram repository root:
 python scripts/ml/validate_ngram_with_openalex.py
Or pin a specific Stage A3 run:
 python scripts/ml/validate_ngram_with_openalex.py --a3-run results/ml/ngram_death_aligned/run_20261017_134328
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BINS = tuple(range(-30, 50, 10))
START_YEAR, END_YEAR = 1800, 2019
REQUIRED_OA = {"artist_id", "year", "works_count", "denominator_works", "is_complete", "normalization_status"}
REQUIRED_A3 = {"artist_id", "death_cluster_id"}
REQUIRED_FEAT = {"artist_id", "death_year", "identity_warning", "eligible"} | {f"rate_rel_{s}" for s in BINS}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_cols(frame: pd.DataFrame, required: set[str], source: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{source}: missing columns {missing}")


def parse_bool(series: pd.Series, what: str) -> pd.Series:
    ss = series.astype("string").str.strip().str.lower()
    good = {"true", "false", "1", "0", "yes", "no"}
    if not ss.isin(good).all():
        raise ValueError(f"{what}: unexpected bool values: {ss[~ss.isin(good)].dropna().unique()[:5]}")
    return ss.isin({"true", "1", "yes"})


def read_inputs(a3_run: Path, openalex: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    ap = a3_run / "death_aligned_assignments.csv"
    fp = a3_run / "death_aligned_features.csv"
    for p in (ap, fp, openalex):
        if not p.is_file():
            raise FileNotFoundError(p)

    assn = pd.read_csv(ap, dtype={"artist_id": "string"})
    feat = pd.read_csv(fp, dtype={"artist_id": "string"})
    assert_cols(assn, REQUIRED_A3, str(ap))
    assert_cols(feat, REQUIRED_FEAT, str(fp))
    for f, name in ((assn, "A3 assignments"), (feat, "A3 features")):
        if f["artist_id"].isna().any() or f["artist_id"].duplicated().any():
            raise ValueError(f"{name}: blank or duplicate artist_id")
    if set(assn.artist_id) != set(feat.artist_id):
        raise ValueError("A3 assignments and A3 features contain different artist IDs")
    assigned = assn.merge(feat, on="artist_id", validate="one_to_one", how="inner")
    assigned["eligible"] = parse_bool(assigned.eligible, "A3 eligible")
    if not assigned["eligible"].all():
        raise ValueError("Some A3 assignments are marked ineligible")
    assigned["identity_warning"] = parse_bool(assigned.identity_warning, "identity_warning")
    assigned["death_year"] = pd.to_numeric(assigned.death_year, errors="raise").astype("int64")
    if assigned.death_year.isna().any():
        raise ValueError("A3 cohort contains missing death_year")
    assigned["death_cluster_id"] = pd.to_numeric(assigned.death_cluster_id, errors="raise").astype("int64")
    for col in (f"rate_rel_{b}" for b in BINS):
        assigned[col] = pd.to_numeric(assigned[col], errors="raise")
        if not np.isfinite(assigned[col]).all() or (assigned[col] < 0).any():
            raise ValueError(f"Invalid A3 Ngram rates in {col}")
    if ((assigned.death_year - 30 < START_YEAR) | (assigned.death_year + 49 > END_YEAR)).any():
        raise ValueError("A3 cohort has years outside the relative comparison window")
    if assigned.death_cluster_id.nunique() < 2:
        raise ValueError("Cannot compare one A3 cluster")

    oa = pd.read_csv(openalex, dtype={"artist_id": "string"})
    assert_cols(oa, REQUIRED_OA, str(openalex))
    if oa.artist_id.isna().any() or oa[["artist_id", "year"]].duplicated().any():
        raise ValueError("OpenAlex duplicate or blank artist-year key")
    oa["year"] = pd.to_numeric(oa.year, errors="raise").astype("int64")
    oa["works_count"] = pd.to_numeric(oa.works_count, errors="raise").astype("int64")
    oa["denominator_works"] = pd.to_numeric(oa.denominator_works, errors="raise").astype("int64")
    if "analysis_works_count" in oa.columns:
        ac = pd.to_numeric(oa.analysis_works_count, errors="raise").astype("int64")
        if (ac != oa.works_count).any():
            raise ValueError("This Stage B protocol requires OpenAlex normalized --types all")
    if (oa.works_count < 0).any() or (oa.denominator_works < 0).any():
        raise ValueError("OpenAlex has negative values")
    if ((oa.year < START_YEAR) | (oa.year > END_YEAR)).any():
        raise ValueError("OpenAlex includes years outside 1800–2019")
    oa["is_complete"] = parse_bool(oa.is_complete, "OpenAlex is_complete")
    if not oa.is_complete.all():
        raise ValueError("OpenAlex contains incomplete artists; no zero imputation allowed")
    ss = oa.normalization_status.astype("string").str.strip().str.lower()
    if (~ss.isin(["ok", "zero_denominator"])).any():
        raise ValueError("Unrecognized OpenAlex normalization_status")
    if ((ss.eq("ok")) != oa.denominator_works.gt(0)).any():
        raise ValueError("OpenAlex denominator/status mismatch")
    if (oa.works_count > oa.denominator_works).any():
        raise ValueError("OpenAlex artist work count exceeds corpus denominator")
    if (oa.groupby("year").denominator_works.nunique() > 1).any():
        raise ValueError("OpenAlex annual corpus denominator changes across artists")

    select = oa[oa.artist_id.isin(set(assigned.artist_id))].copy()
    expected = len(assigned) * (END_YEAR - START_YEAR + 1)
    if len(select) != expected:
        raise ValueError(f"OpenAlex A3 subset has {len(select)} rows, expected {expected} (complete annual grid)")
    missing_ids = set(assigned.artist_id) - set(select.artist_id)
    if missing_ids:
        raise ValueError(f"OpenAlex missing A3 artists {sorted(missing_ids)[:5]}")
    if not (select.groupby("artist_id").year.nunique() == 220).all():
        raise ValueError("OpenAlex artist annual grid is incomplete")
    return assigned.sort_values("artist_id").reset_index(drop=True), select


def construct_relative_rows(assigned: pd.DataFrame, oa: pd.DataFrame) -> pd.DataFrame:
    # Each artist contributes exactly 80 corresponding calendar years, not 220.
    years = (np.array(BINS, dtype=int)[:, None] + np.arange(10, dtype=int)).ravel()
    grid = assigned[["artist_id", "death_year", "death_cluster_id", "identity_warning"]].merge(
        pd.DataFrame({"relative_year": years}), how="cross")
    grid["year"] = grid.death_year + grid.relative_year
    grid = grid.merge(oa[["artist_id", "year", "works_count", "denominator_works"]],
                      on=["artist_id", "year"], how="left", validate="one_to_one")
    if grid[["works_count", "denominator_works"]].isna().any().any():
        raise ValueError("OpenAlex data missing within A3 observable window; cannot fill zeros")
    grid["relative_bin_start"] = -30 + 10 * ((grid.relative_year + 30) // 10)
    rows = grid.groupby(["artist_id", "death_cluster_id", "identity_warning", "relative_bin_start"],
                        as_index=False).agg(works_count=("works_count", "sum"),
                                            denominator_works=("denominator_works", "sum"),
                                            years_observed=("year", "count"))
    if len(rows) != len(assigned) * len(BINS) or not (rows.years_observed == 10).all():
        raise ValueError("Relative time grouping dropped or duplicated years")
    if (rows.denominator_works <= 0).any():
        raise ValueError("At least one observed OA bin lacks a positive denominator; report missingness before comparing")
    rows["works_per_10000"] = 10000 * rows.works_count / rows.denominator_works
    rows = rows.sort_values(["artist_id", "relative_bin_start"]).reset_index(drop=True)
    return rows


def spear(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) != len(b) or len(a) < 3 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy(dtype=float)
    rb = pd.Series(b).rank().to_numpy(dtype=float)
    return float(np.corrcoef(ra, rb)[0, 1])


def per_artist_summary(assigned: pd.DataFrame, rows: pd.DataFrame,
                       min_works: int, min_nonzero_bins: int) -> pd.DataFrame:
    per_artist = []
    grp = rows.groupby("artist_id", sort=False)
    for a in assigned.to_dict(orient="records"):
        group = grp.get_group(a["artist_id"]).sort_values("relative_bin_start")
        nums = group.works_count.to_numpy(dtype=int)
        rates = group.works_per_10000.to_numpy(dtype=float)
        ngram_rates = np.array([a[f"rate_rel_{b}"] for b in BINS], dtype=float)
        works = int(nums.sum())
        nz = int(np.count_nonzero(nums))
        signal = bool(works >= min_works and nz >= min_nonzero_bins)
        # Pre is -30 through -1 (three bins). Post is 0 through +49 (five bins).
        post = int(nums[3:].sum())
        pre = int(nums[:3].sum())
        oa_shape = np.log1p(rates)
        shape_sd = oa_shape.std()
        if signal and shape_sd <= 1e-12:
            signal = False
        rho = spear(ngram_rates, rates) if signal else float("nan")
        item = {
            "artist_id": a["artist_id"],
            "canonical_name": a.get("canonical_name", ""),
            "death_year": a["death_year"],
            "death_cluster_id": a["death_cluster_id"],
            "identity_warning": a["identity_warning"],
            "openalex_window_works": works,
            "openalex_nonzero_bins": nz,
            "openalex_any": works > 0,
            "openalex_signal_sufficient": signal,
            "openalex_predeath_works": pre,
            "openalex_postdeath_works": post,
            "openalex_postdeath_share": post / works if works else np.nan,
            "ngram_openalex_spearman_rho": rho,
        }
        for i, b in enumerate(BINS):
            item[f"openalex_rate_rel_{b}"] = rates[i]
            item[f"ngram_rate_rel_{b}"] = ngram_rates[i]
        per_artist.append(item)
    result = pd.DataFrame(per_artist).sort_values("artist_id").reset_index(drop=True)
    if len(result) != len(assigned):
        raise ValueError("Some artists were dropped from Stage B summary")
    return result


def mean_shape(matrix: np.ndarray) -> np.ndarray:
    if len(matrix) == 0:
        return np.repeat(np.nan, len(BINS))
    y = np.log1p(matrix)
    sd = y.std(axis=1, keepdims=True)
    if (sd <= 1e-12).any():
        raise ValueError("A constant artist series passed the signal filter")
    z = (y - y.mean(axis=1, keepdims=True)) / sd
    return z.mean(axis=0)


def group_profiles(per_artist: pd.DataFrame, bins: pd.DataFrame) -> pd.DataFrame:
    # Note: no inter-source addition of counts. Group median often equals zero due to sparsity.
    profiles = []
    ids_by_cluster = {c: set(g.artist_id) for c, g in per_artist.groupby("death_cluster_id")}
    sig_by_cluster = {c: set(g.loc[g.openalex_signal_sufficient, "artist_id"])
                      for c, g in per_artist.groupby("death_cluster_id")}
    for c, ids in ids_by_cluster.items():
        signal_ids = sig_by_cluster[c]
        for b in BINS:
            sub = bins.loc[(bins.death_cluster_id == c) & (bins.relative_bin_start == b)]
            signal = sub[sub.artist_id.isin(signal_ids)]
            profiles.append({
                "death_cluster_id": c,
                "relative_bin_start": b,
                "artists_all": len(sub),
                "artists_with_work_in_bin": int((sub.works_count > 0).sum()),
                "fraction_with_work_in_bin": float((sub.works_count > 0).mean()),
                "median_rate_all": float(sub.works_per_10000.median()),
                "mean_rate_all": float(sub.works_per_10000.mean()),
                "artists_signal_subset": len(signal),
                "median_rate_signal_subset": float(signal.works_per_10000.median()) if len(signal) else float("nan"),
                "mean_rate_signal_subset": float(signal.works_per_10000.mean()) if len(signal) else float("nan"),
            })
    return pd.DataFrame(profiles)


def group_coverage(per_artist: pd.DataFrame, *, risk_removed: bool = False) -> pd.DataFrame:
    frame = per_artist.loc[~per_artist.identity_warning] if risk_removed else per_artist
    out = []
    for c, g in frame.groupby("death_cluster_id"):
        corr = g.ngram_openalex_spearman_rho.dropna()
        out.append({
            "death_cluster_id": c,
            "artists": len(g),
            "openalex_any_n": int(g.openalex_any.sum()),
            "openalex_any_pct": float(g.openalex_any.mean() * 100),
            "openalex_signal_n": int(g.openalex_signal_sufficient.sum()),
            "openalex_signal_pct": float(g.openalex_signal_sufficient.mean() * 100),
            "median_works_per_artist": float(g.openalex_window_works.median()),
            "median_window_works_positive": float(g.loc[g.openalex_any, "openalex_window_works"].median()) if g.openalex_any.any() else float("nan"),
            "spearman_evaluable_n": len(corr),
            "median_ngram_openalex_spearman": float(corr.median()) if len(corr) else float("nan"),
            "identity_warnings": int(g.identity_warning.sum()),
            "exclude_flags_analysis": risk_removed,
        })
    return pd.DataFrame(out)


def cohort_table(per_artist: pd.DataFrame) -> pd.DataFrame:
    d = per_artist.copy()
    d["death_cohort_start"] = (d.death_year // 25) * 25
    return d.groupby(["death_cohort_start", "death_cluster_id"], as_index=False).agg(
        artists=("artist_id", "count"),
        oa_any=("openalex_any", "sum"),
        oa_signal=("openalex_signal_sufficient", "sum"),
        oa_works_median=("openalex_window_works", "median"),
    ).assign(oa_any_pct=lambda x: x.oa_any / x.artists * 100,
             oa_signal_pct=lambda x: x.oa_signal / x.artists * 100)


def plot_outputs(output: Path, per_artist: pd.DataFrame, profiles: pd.DataFrame,
                 coverage: pd.DataFrame) -> None:
    figdir = output / "figures"
    figdir.mkdir()
    labs = sorted(per_artist.death_cluster_id.unique())
    marks = [b + 4.5 for b in BINS]
    styles = ["-", "--", ":", "-."]

    fig, ax = plt.subplots(figsize=(8.6, 4.7))
    for i, c in enumerate(labs):
        p = profiles[profiles.death_cluster_id == c].sort_values("relative_bin_start")
        ax.plot(marks, p.fraction_with_work_in_bin * 100, linestyle=styles[i % len(styles)],
                marker="o", label=f"Ngram A3 cluster {c} (n={int(p.artists_all.iloc[0])})")
    ax.axvline(0, color="0.5", linewidth=1, linestyle=":")
    ax.set(xlabel="Years relative to death (10-year bin midpoint)", ylabel="Artists with >=1 matching OA work (%)",
           title="OpenAlex mention coverage within the Ngram A3 clusters")
    ax.legend()
    fig.tight_layout(); fig.savefig(figdir / "openalex_bin_prevalence.png", dpi=180); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.6, 4.7))
    for i, c in enumerate(labs):
        p = profiles[profiles.death_cluster_id == c].sort_values("relative_bin_start")
        ax.plot(marks, p.mean_rate_all, linestyle=styles[i % len(styles)], marker="o",
                label=f"Ngram A3 cluster {c}")
    ax.axvline(0, color="0.5", linewidth=1, linestyle=":")
    ax.set(xlabel="Years relative to death (10-year bin midpoint)",
           ylabel="Mean OA works per 10,000 indexed works",
           title="OpenAlex mean rates for all A3 members (zeros included)")
    ax.legend()
    fig.tight_layout(); fig.savefig(figdir / "openalex_rates_by_cluster.png", dpi=180); plt.close(fig)

    fig, axs = plt.subplots(2, 1, figsize=(9, 7.2), sharex=True)
    for i, c in enumerate(labs):
        g = per_artist[(per_artist.death_cluster_id == c) & (per_artist.openalex_signal_sufficient)]
        ax = axs[i] if i < len(axs) else axs[0]
        if len(g):
            xn = g[[f"ngram_rate_rel_{b}" for b in BINS]].to_numpy(float)
            xo = g[[f"openalex_rate_rel_{b}" for b in BINS]].to_numpy(float)
            ax.plot(marks, mean_shape(xn), marker="o", linestyle="-", label="Ngram (per-artist shape)")
            ax.plot(marks, mean_shape(xo), marker="s", linestyle="--", label="OpenAlex (per-artist shape)")
        else:
            ax.text(0.5, 0.5, "No artists pass OpenAlex signal threshold", ha="center", transform=ax.transAxes)
        ax.set(title=f"Ngram A3 cluster {c}: same signal-qualified artists (n={len(g)})",
               ylabel="Mean within-artist z-score")
        ax.axvline(0, color="0.5", linewidth=1, linestyle=":")
        ax.legend(loc="best") if len(g) else None
    axs[-1].set_xlabel("Years relative to death (10-year bin midpoint)")
    fig.tight_layout(); fig.savefig(figdir / "ngram_openalex_shapes.png", dpi=180); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.8, 4.6))
    x = np.arange(len(coverage))
    width = .35
    ax.bar(x - width / 2, coverage.openalex_any_pct, width, label="Any OA match")
    ax.bar(x + width / 2, coverage.openalex_signal_pct, width, label="Pass OA signal threshold", hatch="//")
    ax.set_xticks(x, [f"Cluster {c}" for c in coverage.death_cluster_id])
    ax.set(ylabel="Share of Ngram A3 cluster (%)", title="OpenAlex sample selection by Ngram A3 cluster")
    ax.legend()
    fig.tight_layout(); fig.savefig(figdir / "openalex_coverage_by_cluster.png", dpi=180); plt.close(fig)


def make_unique_directory(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    for i in range(100):
        name = stamp if i == 0 else f"{stamp}_{i}"
        p = root / name
        try:
            p.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return p
    raise RuntimeError("Could not reserve an output directory")


def save_csv(frame: pd.DataFrame, path: Path):
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def serialize(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"JSON serialization unsupported: {type(value)}")


def execute(a3_run: Path, oa_path: Path, out_root: Path,
            min_works: int = 3, min_nonzero_bins: int = 2) -> tuple[Path, dict]:
    assigned, oa = read_inputs(a3_run, oa_path)
    binrows = construct_relative_rows(assigned, oa)
    perartist = per_artist_summary(assigned, binrows, min_works, min_nonzero_bins)
    prof = group_profiles(perartist, binrows)
    cover = group_coverage(perartist)
    cover_no_flags = group_coverage(perartist, risk_removed=True)
    cohor = cohort_table(perartist)
    out = make_unique_directory(out_root)
    save_csv(binrows, out / "openalex_relative_decades.csv")
    save_csv(perartist, out / "cross_source_artist_summary.csv")
    save_csv(prof, out / "group_relative_profiles.csv")
    save_csv(cover, out / "coverage_by_cluster.csv")
    save_csv(cover_no_flags, out / "coverage_by_cluster_no_warnings.csv")
    save_csv(cohor, out / "death_cohort_comparison.csv")
    plot_outputs(out, perartist, prof, cover)
    summary = {
        "stage": "B: independent OpenAlex assessment of fixed A3 Ngram clusters",
        "not_retraining": True,
        "artist_count": len(perartist),
        "ngram_a3_clusters": {str(int(k)): int(v) for k, v in perartist.death_cluster_id.value_counts().sort_index().items()},
        "observed_relative_years_inclusive": [-30, 49],
        "openalex_signal_rule": {"min_window_works": min_works, "min_nonzero_10y_bins": min_nonzero_bins},
        "openalex_artist_with_any_works": int(perartist.openalex_any.sum()),
        "openalex_artist_with_signal": int(perartist.openalex_signal_sufficient.sum()),
        "total_artist_work_matches_in_window": int(perartist.openalex_window_works.sum()),
        "identity_warnings": int(perartist.identity_warning.sum()),
        "by_cluster": cover.replace({np.nan: None}).to_dict(orient="records"),
        "by_cluster_no_warnings": cover_no_flags.replace({np.nan: None}).to_dict(orient="records"),
        "inputs": {"a3_run": str(a3_run.resolve()), "openalex_csv": str(oa_path.resolve())},
        "input_sha256": {
            "a3_assignments": sha256(a3_run / "death_aligned_assignments.csv"),
            "a3_features": sha256(a3_run / "death_aligned_features.csv"),
            "openalex_csv": sha256(oa_path),
        },
        "limitations": [
            "A3 Ngram cluster labels are fixed; OpenAlex is NOT used to train or select K.",
            "OpenAlex English/core/Visual Arts full-name mention proxy is sparse and imperfectly disambiguated.",
            "An OpenAlex zero means no matched work under the collection rules, not proof of absence of research.",
            "Group plots are descriptive; death cohorts and cohort-specific OA corpus size may confound comparisons.",
            "Artists with <=2 OpenAlex works or only one occupied bin are excluded ONLY from shape/correlation analyses; they remain in coverage and main profiles.",
            "Rates are works per 10,000 eligible OA works computed from the SUM of decade numerators and denominators.",
            "Across-source z-scores compare trajectories, not absolute visibility or evidence of causation.",
            "A single work may count for multiple artists; group totals are artist-work matches, not global unique works.",
        ],
    }
    (out / "comparison_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=serialize), encoding="utf-8")
    report = ["# Stage B: Ngram clusters compared with OpenAlex", "",
              "The Ngram Stage A3 cluster labels are fixed. No OpenAlex training was performed.", "",
              f"- A3 artists: **{len(perartist)}**",
              f"- Any OpenAlex work in the aligned 80-year window: **{int(perartist.openalex_any.sum())}**",
              f"- Adequate signal (>= {min_works} works, >= {min_nonzero_bins} distinct ten-year bins): **{int(perartist.openalex_signal_sufficient.sum())}**",
              f"- Manually flagged identity risks from A3: **{int(perartist.identity_warning.sum())}**", "",
              "## Cluster-level summary", "",
              "| A3 cluster | Ngram artists | OA any-work artists | OA signal-qualified artists | Median Ngram–OA Spearman rho (qualified only) |",
              "|---|---:|---:|---:|---:|"]
    for r in cover.to_dict("records"):
        rho = r["median_ngram_openalex_spearman"]
        report.append(f"| {r['death_cluster_id']} | {r['artists']} | {r['openalex_any_n']} | {r['openalex_signal_n']} | {rho:.3f} |" if pd.notna(rho) else
                      f"| {r['death_cluster_id']} | {r['artists']} | {r['openalex_any_n']} | {r['openalex_signal_n']} | N/A |")
    report.extend(["", "## Interpretation safeguards", "",
                   "The OA trend charts are observational corroboration checks, not external ground-truth labels.",
                   "Do not describe differences as causal or as proof of artist identity.",
                   "The OA window contains different calendar years for artists dying in different years.",
                   "Review `death_cohort_comparison.csv` before interpreting group effects.",
                   "The 80-year comparison window is the same for both sources and no outside-window years were treated as zero.",
                   "Artists with insufficient OpenAlex signal are retained in the full cohort coverage tables."])
    (out / "REPORT_STAGE_B.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return out, summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a3-run", type=Path, help="A specific results/ml/ngram_death_aligned/run_... directory")
    p.add_argument("--openalex", type=Path, default=Path("results/openalex_normalized/artist_year_normalized.csv"))
    p.add_argument("--output-root", type=Path, default=Path("results/ml/ngram_openalex_validation"))
    p.add_argument("--min-works", type=int, default=3, help="OA mentions in aligned 80-year window needed for SHAPE comparison")
    p.add_argument("--min-nonzero-bins", type=int, default=2)
    args = p.parse_args()
    if args.min_works < 1 or not 1 <= args.min_nonzero_bins <= 8:
        p.error("min-works >= 1 and min-nonzero-bins within 1..8 required")
    if args.a3_run is None:
        options = sorted(Path("results/ml/ngram_death_aligned").glob("run_*"))
        options = [o for o in options if (o / "death_aligned_assignments.csv").is_file() and (o / "death_aligned_features.csv").is_file()]
        if not options:
            p.error("No A3 runs found; specify --a3-run")
        args.a3_run = options[-1]
    out, summary = execute(args.a3_run, args.openalex, args.output_root,
                           args.min_works, args.min_nonzero_bins)
    print(json.dumps({"output_dir": str(out), "a3_artists": summary["artist_count"],
                      "oa_any_artists": summary["openalex_artist_with_any_works"],
                      "oa_signal_artists": summary["openalex_artist_with_signal"],
                      "a3_groups": summary["ngram_a3_clusters"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

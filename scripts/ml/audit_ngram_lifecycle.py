#!/usr/bin/env python3
"""Stage A2: read-only Ngram K-Means lifecycle and name-quality audit.

Run at any working directory:
    python scripts/ml/audit_ngram_lifecycle.py
Does not modify sources or existing ML baseline; creates a new results directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_denominators(folder: Path) -> pd.DataFrame:
    data = []
    for path in sorted(folder.glob("totalcounts-*")):
        try:
            n = int(path.name.rsplit("-", 1)[1])
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Unexpected denominator filename: {path}") from exc
        for cell in path.read_text(encoding="utf-8").split():
            parts = cell.split(",")
            if len(parts) != 4:
                raise ValueError(f"Invalid denominator entry in {path}: {cell}")
            y, matches, _pages, _volumes = map(int, parts)
            data.append({"n": n, "year": y, "denominator": matches})
    if not data:
        raise FileNotFoundError(f"No totalcounts-* files found in {folder}")
    totals = pd.DataFrame(data)
    if totals.duplicated(["n", "year"]).any():
        raise ValueError("Repeated denominator (n, year)")
    return totals


def read_inputs(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = root / "outputs" / "ngram_artists_full"
    assign_path = root / "results" / "ml" / "ngram_kmeans" / "cluster_assignments.csv"
    assign = pd.read_csv(assign_path, encoding="utf-8-sig", dtype={"artist_id": "string"})
    if "artist_id" not in assign:
        raise ValueError(f"Missing artist_id in {assign_path}")
    cluster_col = next((c for c in ("cluster", "cluster_id", "cluster_label", "kmeans_cluster") if c in assign), None)
    if cluster_col is None:
        raise ValueError(f"Missing cluster column; available fields: {list(assign.columns)}")
    assign = assign[["artist_id", cluster_col]].rename(columns={cluster_col: "cluster"})
    assign["artist_id"] = assign["artist_id"].astype(str).str.strip()
    if assign["artist_id"].isna().any() or assign["artist_id"].eq("").any() or assign["artist_id"].duplicated().any():
        raise ValueError("Invalid or duplicate artist_id in assignments")
    if assign["cluster"].isna().any():
        raise ValueError("Null cluster labels")
    assign["cluster"] = assign["cluster"].astype(str)

    artists = pd.read_json(base / "artists.json")
    if artists["qid"].duplicated().any():
        raise ValueError("Duplicate qid in artists.json")
    dictionary = pd.read_csv(root / "outputs" / "artist_dictionary.csv", encoding="utf-8-sig", dtype={"artist_id": "string"})
    if dictionary["artist_id"].duplicated().any():
        raise ValueError("Duplicate artist_id in dictionary")
    meta = artists[["qid", "n", "query", "query_warning", "status"]].merge(
        dictionary[["artist_id", "canonical_name", "birth_year", "death_year"]],
        left_on="qid", right_on="artist_id", validate="one_to_one", how="left"
    )
    if meta["canonical_name"].isna().any():
        raise ValueError("At least one artist qid missing in dictionary")
    for fld in ("birth_year", "death_year"):
        meta[fld] = pd.to_numeric(meta[fld], errors="coerce")
    members = assign.merge(meta, on="artist_id", how="left", validate="one_to_one")
    if members["qid"].isna().any():
        raise ValueError("Cluster assignments contain IDs absent from artists.json")

    rows = pd.read_json(base / "artist_year_counts.jsonl", lines=True)
    required = {"artist_id", "query", "n", "year", "match_count"}
    if not required.issubset(rows.columns):
        raise ValueError(f"Missing Ngram fields: {sorted(required - set(rows.columns))}")
    if rows.duplicated(["artist_id", "query", "year"]).any():
        raise ValueError("Duplicate (artist_id, query, year) in Ngram records")
    if (rows["match_count"] < 0).any() or (~rows["year"].between(1800, 2019)).any():
        raise ValueError("Invalid match count or year outside 1800–2019")
    rows = rows[rows["artist_id"].isin(members["artist_id"])].copy()
    rows = rows.merge(members[["artist_id", "n", "query", "birth_year", "death_year"]], on="artist_id", suffixes=("", "_meta"), validate="many_to_one")
    mismatch = (rows["n"] != rows["n_meta"]) | (rows["query"] != rows["query_meta"])
    if mismatch.any():
        raise ValueError("Artist query or n mismatch between raw rows and artist metadata")
    if rows.empty:
        raise ValueError("No Ngram rows for clustered artists")
    totals = read_denominators(base / "totalcounts")
    rows = rows.merge(totals, on=["n", "year"], how="left", validate="many_to_one")
    if rows["denominator"].isna().any() or (rows["denominator"] <= 0).any():
        bad = rows.loc[rows["denominator"].isna() | (rows["denominator"] <= 0), ["artist_id", "n", "year"]].head(5)
        raise ValueError(f"Missing/zero Ngram denominators: {bad.to_dict('records')}")
    rows["rate_per_billion"] = rows["match_count"] / rows["denominator"] * 1e9
    return members, rows, dictionary


def cramer_v(table: pd.DataFrame) -> float | None:
    observed = table.to_numpy(dtype=float)
    if observed.shape[0] < 2 or observed.shape[1] < 2:
        return None
    total = observed.sum()
    expected = np.outer(observed.sum(axis=1), observed.sum(axis=0)) / total
    nonzero = expected > 0
    chi2 = float(np.sum((observed[nonzero] - expected[nonzero]) ** 2 / expected[nonzero]))
    return math.sqrt(chi2 / (total * min(observed.shape[0]-1, observed.shape[1]-1)))


def plot_year_hist(audit: pd.DataFrame, field: str, path: Path) -> None:
    groups = sorted(audit["cluster"].unique())
    data = [audit.loc[audit["cluster"].eq(c), field].dropna().to_numpy() for c in groups]
    all_values = pd.concat([audit[field].dropna()])
    if all_values.empty:
        return
    lo = (int(all_values.min()) // 25) * 25
    hi = (int(all_values.max()) // 25 + 2) * 25
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(data, bins=range(lo, hi + 1, 25), label=[f"Cluster {c} (n={len(v)})" for c, v in zip(groups, data)], alpha=0.7)
    ax.set(xlabel=field.replace("_", " ").title(), ylabel="Artists", title=f"{field.replace('_', ' ').title()} by Ngram cluster")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def analyze(root: Path, output_parent: Path | None = None) -> Path:
    members, rows, _dictionary = read_inputs(root)
    if output_parent is None:
        output_parent = root / "results" / "ml"
    base = output_parent / "ngram_lifecycle_audit"
    if base.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = output_parent / f"ngram_lifecycle_audit_{stamp}"
        suffix = 1
        while base.exists():
            base = output_parent / f"ngram_lifecycle_audit_{stamp}_{suffix}"
            suffix += 1
    figures = base / "figures"
    figures.mkdir(parents=True)

    rows["prebirth_count"] = np.where(rows["birth_year"].notna() & rows["year"].lt(rows["birth_year"]), rows["match_count"], 0)
    sums = rows.groupby("artist_id", as_index=False).agg(total_match_count=("match_count", "sum"), prebirth_match_count=("prebirth_count", "sum"))
    peaks = (rows.sort_values(["artist_id", "rate_per_billion", "year"], ascending=[True, False, True])
             .drop_duplicates("artist_id")[["artist_id", "year", "rate_per_billion"]]
             .rename(columns={"year": "peak_year_normalized", "rate_per_billion": "peak_rate_per_billion"}))
    audit = members.merge(sums, on="artist_id", how="left", validate="one_to_one").merge(peaks, on="artist_id", how="left", validate="one_to_one")
    if audit["total_match_count"].isna().any() or (audit["total_match_count"] <= 0).any():
        raise ValueError("One or more assigned artists have no positive Ngram records")
    audit["prebirth_match_share"] = audit["prebirth_match_count"] / audit["total_match_count"]
    audit["peak_relative_to_birth"] = audit["peak_year_normalized"] - audit["birth_year"]
    audit["peak_relative_to_death"] = audit["peak_year_normalized"] - audit["death_year"]
    audit["peak_before_birth"] = audit["birth_year"].notna() & audit["peak_year_normalized"].lt(audit["birth_year"])
    audit["prebirth_over_1pct"] = audit["birth_year"].notna() & audit["prebirth_match_share"].gt(0.01)
    audit["invalid_lifespan"] = audit["birth_year"].notna() & audit["death_year"].notna() & audit["death_year"].lt(audit["birth_year"])
    def reasons(r: pd.Series) -> str:
        flags = []
        if pd.isna(r["birth_year"]): flags.append("missing_birth_year")
        if pd.isna(r["death_year"]): flags.append("missing_death_year")
        if r["prebirth_over_1pct"]: flags.append("prebirth_share_over_1pct_review_only")
        if r["peak_before_birth"]: flags.append("normalized_annual_peak_before_birth_review_only")
        if r["invalid_lifespan"]: flags.append("death_before_birth")
        if pd.notna(r["query_warning"]) and str(r["query_warning"]).strip(): flags.append("source_query_warning")
        return ";".join(flags)
    audit["warning_reason"] = audit.apply(reasons, axis=1)
    audit["identity_warning"] = audit["prebirth_over_1pct"] | audit["peak_before_birth"] | audit["invalid_lifespan"] | audit["query_warning"].notna() & audit["query_warning"].astype(str).str.strip().ne("")
    audit = audit.sort_values(["cluster", "artist_id"]).reset_index(drop=True)
    audit_columns = ["artist_id", "canonical_name", "cluster", "birth_year", "death_year", "total_match_count", "prebirth_match_count", "prebirth_match_share", "peak_year_normalized", "peak_rate_per_billion", "peak_relative_to_birth", "peak_relative_to_death", "peak_before_birth", "identity_warning", "warning_reason", "query", "n", "query_warning"]
    audit[audit_columns].to_csv(base / "artist_lifecycle_audit.csv", index=False)
    audit.sort_values(["prebirth_match_share", "prebirth_match_count"], ascending=[False, False]).head(20)[audit_columns].to_csv(base / "top_20_review_cases.csv", index=False)

    present_birth = audit.dropna(subset=["birth_year"]).copy()
    present_birth["cohort_start"] = (present_birth["birth_year"].astype(int) // 25) * 25
    cohorts = pd.crosstab(present_birth["cohort_start"], present_birth["cluster"])
    proportions = cohorts.div(cohorts.sum(axis=1), axis=0)
    tidy = cohorts.stack().rename("artist_count").reset_index()
    tidy["cohort_end"] = tidy["cohort_start"] + 24
    tidy["cohort_total"] = tidy["cohort_start"].map(cohorts.sum(axis=1))
    tidy["within_cohort_share"] = tidy["artist_count"] / tidy["cohort_total"]
    tidy[["cohort_start", "cohort_end", "cluster", "artist_count", "cohort_total", "within_cohort_share"]].to_csv(base / "birth_cohort_comparison.csv", index=False)

    plot_year_hist(audit, "birth_year", figures / "birth_year_distribution.png")
    plot_year_hist(audit, "death_year", figures / "death_year_distribution.png")
    fig, ax = plt.subplots(figsize=(11, 5))
    bottom = np.zeros(len(proportions))
    for c in proportions.columns:
        vals = proportions[c].to_numpy()
        ax.bar(proportions.index.astype(str), vals, bottom=bottom, label=f"Cluster {c}")
        bottom += vals
    ax.set(xlabel="Birth cohort (25 years)", ylabel="Within-cohort share", title="Cluster composition within birth cohorts", ylim=(0, 1))
    ax.legend()
    ax.tick_params(axis="x", labelrotation=55)
    fig.tight_layout()
    fig.savefig(figures / "birth_cohort_cluster_share.png", dpi=170)
    plt.close(fig)

    cluster_counts = {str(k): int(v) for k, v in audit["cluster"].value_counts().sort_index().items()}
    medians = {}
    for c, g in audit.groupby("cluster"):
        medians[str(c)] = {key: (None if g[key].dropna().empty else float(g[key].median())) for key in ("birth_year", "death_year", "peak_relative_to_death")}
    summary = {
        "input_baseline": "results/ml/ngram_kmeans/cluster_assignments.csv",
        "artists_in_cluster_baseline": int(len(audit)),
        "cluster_sizes": cluster_counts,
        "birth_year_available": int(audit["birth_year"].notna().sum()),
        "death_year_available": int(audit["death_year"].notna().sum()),
        "artists_with_any_prebirth_hits": int(audit["prebirth_match_count"].gt(0).sum()),
        "artists_prebirth_share_over_1pct": int(audit["prebirth_over_1pct"].sum()),
        "artists_peak_before_birth": int(audit["peak_before_birth"].sum()),
        "artists_flagged_for_identity_review": int(audit["identity_warning"].sum()),
        "birth_cohort_25yr_cramers_v": cramer_v(cohorts),
        "cluster_medians": medians,
        "limitations": ["Warnings are not proven wrong identities; annual peak may be dominated by tiny counts or early-year denominator effects.",
                        "Clustering was NOT rerun; association with birth cohort is descriptive, not causal.",
                        "Yearly peak uses observed positive-name-match years only, with earliest-year tie-breaking.",
                        "Unlisted years in a complete exact-name shard can imply no stored matches, not zero historical recognition."]
    }
    (base / "lifecycle_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    doc = ["# Stage A2 — Ngram lifecycle and name-quality audit", "", f"- Included cluster assignments: **{len(audit)}**; group sizes: **{cluster_counts}**.",
           f"- Birth year available: {summary['birth_year_available']}; death year available: {summary['death_year_available']}.",
           f"- Artists with any pre-birth exact-name hits: **{summary['artists_with_any_prebirth_hits']}**.",
           f"- Artists with pre-birth hit share > 1%: **{summary['artists_prebirth_share_over_1pct']}** (review flag, not identity rejection).",
           f"- Artists with annual normalized peak before birth: **{summary['artists_peak_before_birth']}** (review flag).",
           f"- 25-year birth-cohort association (Cramer's V): **{summary['birth_cohort_25yr_cramers_v']}**.", "",
           "## Interpretation", "", "Cramer's V is descriptive association, not proof of a historical mechanism. Earlier and later-born artists may differ in observation windows. The original K-Means baseline was not modified. Review flagged high-risk names without automatically excluding them.", "",
           "## Outputs", "", "- artist_lifecycle_audit.csv — one row per clustered artist, including birth/death, normalized annual peak, and warnings", "- top_20_review_cases.csv — ordered by pre-birth match share", "- birth_cohort_comparison.csv — cluster counts and within-cohort proportions", "- figures/ — histograms and cohort-composition chart", "- lifecycle_summary.json — validated aggregate counts"]
    (base / "README_audit.md").write_text("\n".join(doc) + "\n", encoding="utf-8")
    return base


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=project_root(), help="Path to project root; defaults to script's parent project")
    p.add_argument("--output-parent", type=Path, default=None, help="Optional parent for a new audit output directory")
    args = p.parse_args()
    out = analyze(args.root.resolve(), args.output_parent.resolve() if args.output_parent else None)
    print(f"Stage A2 created: {out}")
    print((out / "lifecycle_summary.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

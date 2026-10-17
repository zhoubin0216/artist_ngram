#!/usr/bin/env python3
"""Stage A3: comparable calendar/death-aligned Ngram shape K-Means.

Read-only wrt existing project inputs and Stage A1/A2 outputs. Run from repository
root, with --assume-unlisted-zero to document the exact-query sparsity assumption.
Requires the existing scripts/ml/cluster_ngram.py (Stage A1).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import adjusted_rand_score

import cluster_ngram as a1

BINS = list(range(-30, 50, 10))    # eight bins: [-30,-21] ... [+40,+49]
CAL_DECADES = a1.DECADES


def read_unique_csv(path: Path, key: str) -> dict[str, dict]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if key not in (reader.fieldnames or []):
            raise ValueError(f"{path}: missing required column {key!r}")
        result = {}
        for row in reader:
            k = str(row[key]).strip()
            if not k or k in result:
                raise ValueError(f"{path}: blank or duplicate {key}: {k!r}")
            result[k] = row
    return result


def flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in {"true", "1", "yes"}:
        return True
    if str(value).strip().lower() in {"false", "0", "no", "", "none"}:
        return False
    raise ValueError(f"Invalid identity_warning value: {value!r}")


def lifespan_int(a: dict, name: str) -> int | None:
    v = a.get(name)
    if v in ("", None):
        return None
    n = int(v)
    if not (1000 <= n <= 2100):
        raise ValueError(f"Unreasonable {name}={n} for artist {a.get('qid')}")
    return n


def bin_rates(observations: dict[int, int], n: int, death: int,
              denominators: dict[tuple[int, int], int]) -> tuple[list[float], list[int]]:
    """Rebuild each relative ten-year bin FROM ANNUAL COUNTS, never shift A1 bins."""
    rates, counts = [], []
    for start in BINS:
        years = range(death + start, death + start + 10)
        numerator = sum(observations.get(y, 0) for y in years)
        denominator = sum(denominators[(n, y)] for y in years)
        if denominator <= 0:
            raise ValueError(f"Bad denominator for death={death}, relative_bin={start}")
        counts.append(numerator)
        rates.append(float(1e9 * numerator / denominator))
    return rates, counts


def shape_matrix(rows: list[dict], rate_cols: list[str]) -> np.ndarray:
    rates = np.array([[float(r[col]) for col in rate_cols] for r in rows], dtype=float)
    if rates.ndim != 2 or not np.isfinite(rates).all() or (rates < 0).any():
        raise ValueError("Invalid rate feature matrix")
    transformed = np.log1p(rates)
    sd = transformed.std(axis=1, keepdims=True)
    if (sd < 1e-12).any():
        raise ValueError("Constant shape encountered after eligibility checks")
    return (transformed - transformed.mean(axis=1, keepdims=True)) / sd


def make_calendar_rows(a1_all_rows: list[dict], ids: list[str]) -> list[dict]:
    indexed = {r["artist_id"]: r for r in a1_all_rows}
    if len(indexed) != len(a1_all_rows):
        raise ValueError("Duplicate artist IDs in A1 feature data")
    absent = [i for i in ids if i not in indexed]
    if absent:
        raise ValueError(f"Artists missing A1 feature data: {absent[:4]}")
    return [indexed[i] for i in ids]


def compute_cohort(artists: list[dict], a2: dict, baseline: dict, sparse, totals,
                   *, min_nonzero_bins: int, min_total_matches: int):
    a_by_id = {str(a["qid"]): a for a in artists}
    if set(a2) != set(baseline):
        raise ValueError("Stage A2 artist IDs differ from A1 baseline cluster IDs")
    if set(a2) - set(a_by_id):
        raise ValueError("Stage A2 includes artist IDs missing from Ngram artists.json")
    allowed, status = [], []
    for aid in sorted(a2):
        a = a_by_id[aid]
        old = baseline[aid]
        audit = a2[aid]
        if str(old.get("cluster_id", "")).strip() != str(audit.get("cluster", "")).strip():
            raise ValueError(f"A1 and A2 cluster disagree for {aid}")
        birth, death = lifespan_int(a, "birth_year"), lifespan_int(a, "death_year")
        if audit.get("death_year") not in (None, "") and death != int(audit["death_year"]):
            raise ValueError(f"A2 death_year differs from original Ngram artist list for {aid}")
        if audit.get("birth_year") not in (None, "") and birth != int(audit["birth_year"]):
            raise ValueError(f"A2 birth_year differs from original Ngram artist list for {aid}")
        risk = flag(audit.get("identity_warning", ""))
        s = {
            "artist_id": aid, "canonical_name": a["query"],
            "birth_year": birth if birth is not None else "",
            "death_year": death if death is not None else "",
            "a1_cluster_id": int(old["cluster_id"]),
            "identity_warning": risk, "warning_reason": audit.get("warning_reason", ""),
            "complete_window": bool(death is not None and death - 30 >= a1.START and death + 49 <= a1.END),
            "window_match_count": "", "nonzero_relative_bins": "", "eligible": False,
            "exclusion_reason": ""
        }
        reasons = []
        if a["status"] != "matched":
            reasons.append("not_matched")
        if death is None:
            reasons.append("death_year_missing")
        elif not s["complete_window"]:
            reasons.append("window_outside_1800_2019")
        if not reasons:
            rates, counts = bin_rates(sparse.get(aid, {}), int(a["n"]), death, totals)
            n_counts = sum(counts)
            nz = sum(x > 0 for x in counts)
            s["window_match_count"] = n_counts
            s["nonzero_relative_bins"] = nz
            if nz < min_nonzero_bins:
                reasons.append("too_few_nonzero_relative_bins")
            if n_counts < min_total_matches:
                reasons.append("too_few_window_matches")
            if float(np.std(np.log1p(rates))) < 1e-12:
                reasons.append("constant_relative_shape")
            if not reasons:
                s["eligible"] = True
                r = {**s, "n": int(a["n"])}
                r.update({f"rate_rel_{x}": rate for x, rate in zip(BINS, rates)})
                allowed.append(r)
        s["exclusion_reason"] = ";".join(reasons)
        status.append(s)
    if len(allowed) < 4:
        raise ValueError("Not enough eligible artists after applying A3 windows and signal rules")
    return allowed, status


def cluster(x: np.ndarray, args, label: str):
    chosen, metrics, model = a1.cluster_and_evaluate(
        x, max_k=args.max_k, min_cluster_size=args.min_cluster_size,
        random_state=args.seed, stability_runs=args.stability_runs)
    for row in metrics:
        row["experiment"] = label
    return chosen, metrics, model


def contingency(lab1, lab2) -> tuple[list[int], list[int], np.ndarray]:
    ids1, ids2 = sorted(set(map(int, lab1))), sorted(set(map(int, lab2)))
    tab = np.zeros((len(ids1), len(ids2)), dtype=int)
    loc1 = {v: k for k, v in enumerate(ids1)}
    loc2 = {v: k for k, v in enumerate(ids2)}
    for a, b in zip(lab1, lab2):
        tab[loc1[int(a)], loc2[int(b)]] += 1
    return ids1, ids2, tab


def render(output: Path, m_death, m_cal, labels_death, labels_cal, metrics,
           risk_metrics, risk_n):
    fig_dir = output / "figures"
    fig_dir.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for lab in sorted(set(map(int, labels_death))):
        ax.plot([x + 4.5 for x in BINS], m_death.cluster_centers_[lab], marker="o",
                label=f"Cluster {lab} (n={int(sum(labels_death == lab))})")
    ax.axvline(0, color="grey", linestyle="--", linewidth=1, label="Death (0)")
    ax.set(xlabel="Years relative to death (bin midpoint)", ylabel="Within-artist z-score of log1p(rate)",
           title="Death-aligned Ngram visibility shapes")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(fig_dir / "death_aligned_centers.png", dpi=170); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.8))
    for lab in sorted(set(map(int, labels_cal))):
        ax.plot(CAL_DECADES, m_cal.cluster_centers_[lab], marker=".",
                label=f"Cluster {lab} (n={int(sum(labels_cal == lab))})")
    ax.set(xlabel="Calendar decade starting year", ylabel="Within-artist z-score of log1p(rate)",
           title="Calendar-time clusters on EXACT SAME artist cohort")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(fig_dir / "calendar_subset_centers.png", dpi=170); plt.close(fig)

    lhs, rhs, tab = contingency(labels_cal, labels_death)
    fig, ax = plt.subplots(figsize=(6.5, 5))
    im = ax.imshow(tab, cmap="Blues")
    ax.set(xticks=range(len(rhs)), xticklabels=rhs, yticks=range(len(lhs)), yticklabels=lhs,
           xlabel="Death-aligned cluster ID", ylabel="Calendar cluster ID",
           title="Cluster membership comparison (same artists)")
    for (i, j), n in np.ndenumerate(tab):
        ax.text(j, i, str(int(n)), ha="center", va="center",
                color="white" if n > tab.max()/2 else "black")
    fig.colorbar(im, ax=ax, label="Number of artists")
    fig.tight_layout(); fig.savefig(fig_dir / "cluster_comparison_heatmap.png", dpi=170); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    kinds = ["death_aligned", "calendar_same_cohort"]
    all_scores = [next(z["silhouette_score"] for z in metrics if z["selected"] and z["experiment"] == k) for k in kinds]
    risk_scores = [next((z["silhouette_score"] for z in risk_metrics if z["selected"] and z["experiment"] == k + "_unflagged"), float('nan')) for k in kinds]
    pos = np.arange(2)
    ax.bar(pos - .18, all_scores, .36, label="Full eligible cohort")
    ax.bar(pos + .18, risk_scores, .36, label=f"Unflagged only (n={risk_n})")
    ax.set(xticks=pos, xticklabels=["Death-aligned", "Calendar"],
           ylabel="Silhouette score (not comparable across feature spaces as a quality ranking)",
           title="Sensitivity to identity-warning flags")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(fig_dir / "sensitivity_comparison.png", dpi=170); plt.close(fig)


def alloc_output(base: Path) -> Path:
    """Never overwrite an existing A1, A2 or A3 run."""
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    for i in range(100):
        path = base / (stamp if i == 0 else f"{stamp}_{i}")
        try:
            path.mkdir(exist_ok=False)
            return path
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not allocate unique run under {base}")


def run(args):
    root = Path(args.root).resolve()
    if not args.assume_unlisted_zero:
        raise ValueError("Pass --assume-unlisted-zero explicitly (same as A1)")
    a2_path = Path(args.a2_csv)
    if not a2_path.is_absolute():
        a2_path = root / a2_path
    baseline_path = root / "results/ml/ngram_kmeans/cluster_assignments.csv"
    a2 = read_unique_csv(a2_path, "artist_id")
    baseline = read_unique_csv(baseline_path, "artist_id")
    if not {"cluster", "identity_warning", "death_year"}.issubset(next(iter(a2.values()), {})):
        raise ValueError("A2 CSV missing cluster/identity_warning/death_year; use Stage A2 audit output")
    if "cluster_id" not in next(iter(baseline.values()), {}):
        raise ValueError("A1 baseline assignments CSV missing cluster_id")
    artists, sparse, totals, hashes, report = a1.load_inputs(root)
    a1_all_rows, _, _ = a1.make_decade_features(artists, sparse, totals,
        min_nonzero_decades=args.min_nonzero_bins, min_total_matches=args.min_total_matches,
        assume_unlisted_zero=True)
    allowed, eligibility = compute_cohort(
        artists, a2, baseline, sparse, totals,
        min_nonzero_bins=args.min_nonzero_bins, min_total_matches=args.min_total_matches)
    ids = [r["artist_id"] for r in allowed]
    calendar = make_calendar_rows(a1_all_rows, ids)
    x_death = shape_matrix(allowed, [f"rate_rel_{b}" for b in BINS])
    x_cal = a1.normalized_shape_matrix(calendar)
    if len(x_death) != len(x_cal) or len(x_cal) != len(ids):
        raise ValueError("Calendar and death-aligned cohort mismatch")
    c_death, met_death, mod_death = cluster(x_death, args, "death_aligned")
    c_cal, met_cal, mod_cal = cluster(x_cal, args, "calendar_same_cohort")
    labs_d = np.asarray(mod_death.labels_, dtype=int)
    labs_c = np.asarray(mod_cal.labels_, dtype=int)
    old_labels = np.array([int(baseline[i]["cluster_id"]) for i in ids])
    keep = np.array([not bool(r["identity_warning"]) for r in allowed])
    risk_metrics = []
    risk_models = {}
    if int(keep.sum()) >= max(2*args.min_cluster_size, 4):
        for kind, x in [("death_aligned", x_death), ("calendar_same_cohort", x_cal)]:
            try:
                c, m, model = cluster(x[keep], args, kind + "_unflagged")
                risk_metrics.extend(m)
                risk_models[kind] = {"selected_k": c["k"], "silhouette": c["silhouette_score"],
                    "stability_mean_ARI": c["mean_pairwise_ARI"],
                    "ARI_vs_full_cohort_clusters_on_unflagged_subset": float(adjusted_rand_score(
                        model.labels_, (labs_d if kind == "death_aligned" else labs_c)[keep]))}
            except ValueError as exc:
                risk_models[kind] = {"status": "unavailable", "reason": str(exc)}
    output = alloc_output(root / args.output if not Path(args.output).is_absolute() else Path(args.output))
    common = ["artist_id", "canonical_name", "birth_year", "death_year", "a1_cluster_id",
              "identity_warning", "warning_reason", "complete_window", "window_match_count",
              "nonzero_relative_bins", "eligible", "exclusion_reason"]
    a1.write_csv(output / "cohort_eligibility.csv", eligibility, common)
    a1.write_csv(output / "death_aligned_features.csv", allowed,
                 common + ["n"] + [f"rate_rel_{b}" for b in BINS])
    cal_common = ["artist_id", "canonical_name", "n", "birth_year", "total_match_count", "nonzero_decades"]
    a1.write_csv(output / "calendar_subset_features.csv", calendar,
                 cal_common + [f"rate_{y}" for y in CAL_DECADES])
    assignments_d = [{"artist_id": i, "death_cluster_id": int(l), "a1_cluster_id": int(o)}
                     for i, l, o in zip(ids, labs_d, old_labels)]
    assignments_c = [{"artist_id": i, "calendar_cluster_id": int(l), "a1_cluster_id": int(o)}
                     for i, l, o in zip(ids, labs_c, old_labels)]
    a1.write_csv(output / "death_aligned_assignments.csv", assignments_d,
                 ["artist_id", "death_cluster_id", "a1_cluster_id"])
    a1.write_csv(output / "calendar_subset_assignments.csv", assignments_c,
                 ["artist_id", "calendar_cluster_id", "a1_cluster_id"])
    metric_cols = ["experiment", "k", "silhouette_score", "mean_pairwise_ARI", "inertia",
                   "min_cluster_size", "cluster_sizes", "size_constraint_pass", "selected"]
    a1.write_csv(output / "clustering_metrics.csv", met_death + met_cal + risk_metrics, metric_cols)
    # Do not interpret numeric cluster IDs across models as the same semantic classes.
    lhs, rhs, tab = contingency(labs_c, labs_d)
    a1.write_csv(output / "cluster_comparison_table.csv",
                 [{"calendar_cluster_id": a, "death_cluster_id": b, "artists": int(tab[i, j])}
                  for i, a in enumerate(lhs) for j, b in enumerate(rhs)],
                 ["calendar_cluster_id", "death_cluster_id", "artists"])
    render(output, mod_death, mod_cal, labs_d, labs_c, met_death + met_cal, risk_metrics, int(keep.sum()))
    summary = {
        "study": "Stage A3: Ngram calendar vs death-aligned shape K-Means",
        "window_inclusive": {"relative_start": -30, "relative_end": 49,
                             "bins_relative_start": BINS, "years_per_bin": 10},
        "selection": {"a2_baseline_artists": len(a2),
                      "complete_window_artists": sum(r["complete_window"] for r in eligibility),
                      "eligible_for_both_models": len(allowed),
                      "ineligible_reasons": dict(Counter(
                           z for r in eligibility for z in r["exclusion_reason"].split(";") if z)),
                      "identity_warnings_in_eligible": int((~keep).sum()),
                      "unflagged_eligible": int(keep.sum())},
        "death_aligned": {"k": c_death["k"], "silhouette": c_death["silhouette_score"],
                           "initialization_ARI": c_death["mean_pairwise_ARI"],
                           "cluster_sizes": dict(sorted(Counter(map(int, labs_d)).items()))},
        "calendar_same_cohort": {"k": c_cal["k"], "silhouette": c_cal["silhouette_score"],
                           "initialization_ARI": c_cal["mean_pairwise_ARI"],
                           "cluster_sizes": dict(sorted(Counter(map(int, labs_c)).items()))},
        "comparison": {"ARI_calendar_vs_death": float(adjusted_rand_score(labs_c, labs_d)),
                       "ARI_original_A1_vs_calendar_subset": float(adjusted_rand_score(old_labels, labs_c)),
                       "ARI_original_A1_vs_death": float(adjusted_rand_score(old_labels, labs_d))},
        "sensitivity_unflagged": risk_models,
        "configuration": {"min_nonzero_bins": args.min_nonzero_bins, "min_total_matches": args.min_total_matches,
                          "max_k": args.max_k, "min_cluster_size": args.min_cluster_size,
                          "seed": args.seed, "stability_runs": args.stability_runs},
        "input_sha256": {**hashes, "a2_csv": a1.sha256(a2_path),
                         "a1_assignments": a1.sha256(baseline_path)},
        "limits": [
            "Both clustering methods use the EXACT SAME eligible artist IDs but different time axes and feature dimensions.",
            "All relative bins are within the observed 1800-2019 window; outside years are NEVER filled with zero.",
            "Unlisted years inside a completely scanned matched exact-name series are zero matches only as an explicit modelling assumption.",
            "Warnings are review flags, not confirmed false identities; the unflagged sensitivity refits models.",
            "Initialisation ARI does not test temporal or source-data perturbations.",
            "Calendar and death-aligned silhouettes are calculated in different feature spaces and should not be ranked as if directly comparable.",
            "Death-relative binning changes the interpretation of the analysis; it does not resolve same-name confusion."]}
    (output / "comparison_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output), "eligible": len(allowed),
                      "full_window": summary["selection"]["complete_window_artists"],
                      "death_k": c_death["k"], "calendar_k": c_cal["k"],
                      "death_silhouette": round(c_death["silhouette_score"], 4),
                      "ARI_calendar_vs_death": round(summary["comparison"]["ARI_calendar_vs_death"], 4)}, indent=2))
    return summary, output


def cli(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".", help="Path to artist_ngram project root")
    p.add_argument("--a2-csv", default="results/ml/ngram_lifecycle_audit/artist_lifecycle_audit.csv")
    p.add_argument("--output", default="results/ml/ngram_death_aligned", help="Creates unique run_* child")
    p.add_argument("--assume-unlisted-zero", action="store_true", help="REQUIRED: see A1 README")
    p.add_argument("--min-nonzero-bins", type=int, default=4)
    p.add_argument("--min-total-matches", type=int, default=20)
    p.add_argument("--min-cluster-size", type=int, default=10)
    p.add_argument("--max-k", type=int, default=6)
    p.add_argument("--stability-runs", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)
    if args.min_nonzero_bins < 1 or args.min_nonzero_bins > len(BINS) or args.min_total_matches < 1 or args.min_cluster_size < 2 or args.max_k < 2 or args.stability_runs < 2:
        p.error("Invalid selection/clustering parameters")
    return args


if __name__ == "__main__":
    try:
        run(cli())
    except (ValueError, FileNotFoundError) as exc:
        sys.exit(f"ERROR: {exc}")

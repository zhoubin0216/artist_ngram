#!/usr/bin/env python3
"""Reproducible Google Books Ngram decade-shape K-Means baseline.

Run from the artist_ngram repository root. This program NEVER modifies inputs.
The mandatory --assume-unlisted-zero flag records the modelling choice that an
absent year in a COMPLETELY EXTRACTED matched artist's sparse series has zero
recorded matches in the exact-query Ngram dataset. Not-found artists are never
silently converted into zero trajectories or assigned a cluster.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score

START, END = 1800, 2019
DECADES = list(range(1800, 2020, 10))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()



def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_totalcounts(folder: Path, needed_n: set[int]) -> dict[tuple[int, int], int]:
    """Ngram totalcounts: whitespace-separated year,match_count,page_count,volume_count."""
    totals: dict[tuple[int, int], int] = {}
    for n in sorted(needed_n):
        p = folder / f"totalcounts-{n}"
        if not p.is_file():
            raise ValueError(f"Missing exact-n denominator: {p}")
        for cell in p.read_text(encoding="utf-8").split():
            parts = cell.split(",")
            if len(parts) != 4:
                raise ValueError(f"Invalid totalcounts token in {p}: {cell!r}")
            year, count, _, _ = map(int, parts)
            if START <= year <= END:
                key = (n, year)
                if key in totals:
                    raise ValueError(f"Duplicate denominator for {key}")
                if count <= 0:
                    raise ValueError(f"Non-positive denominator for {key}")
                totals[key] = count
        absent = [y for y in range(START, END + 1) if (n, y) not in totals]
        if absent:
            raise ValueError(f"Missing denominator years for n={n}: {absent[:12]}")
    return totals


def load_inputs(root: Path):
    folder = root / "outputs" / "ngram_artists_full"
    artists_file = folder / "artists.json"
    records_file = folder / "artist_year_counts.jsonl"
    report_file = folder / "report.json"
    for p in (artists_file, records_file, report_file):
        if not p.is_file():
            raise FileNotFoundError(p)
    artists = json.loads(artists_file.read_text(encoding="utf-8"))
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if report.get("run_status") != "complete" or report.get("failed_shards"):
        raise ValueError("Ngram extraction report is not complete; refuse zero-fill")
    if report.get("start_year") != START or report.get("end_year") != END:
        raise ValueError("Unexpected time range in report")
    if report.get("artist_count") != len(artists):
        raise ValueError("Artist count differs from extraction report")
    if len({str(a["qid"]) for a in artists}) != len(artists):
        raise ValueError("Duplicate artist IDs in artists.json")
    by_artist = {str(a["qid"]): a for a in artists}
    for a in artists:
        if a.get("status") not in ("matched", "not_found"):
            raise ValueError(f"Unexpected extraction status for {a['qid']}: {a.get('status')}")
        if not isinstance(a.get("n"), int) or a["n"] <= 0:
            raise ValueError(f"Invalid n-gram order for {a['qid']}")
    totals = load_totalcounts(folder / "totalcounts", {int(a["n"]) for a in artists})
    sparse: dict[str, dict[int, int]] = defaultdict(dict)
    rows_count = 0
    with records_file.open(encoding="utf-8-sig") as handle:
        for lineno, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            rec = json.loads(line)
            artist_id, year = str(rec["artist_id"]), int(rec["year"])
            if artist_id not in by_artist:
                raise ValueError(f"Line {lineno}: unknown artist_id {artist_id}")
            artist = by_artist[artist_id]
            if artist.get("status") != "matched":
                raise ValueError(f"Line {lineno}: row for a not-found artist {artist_id}")
            if int(rec["n"]) != int(artist["n"]) or rec["query"] != artist["query"]:
                raise ValueError(f"Line {lineno}: query/n-gram mismatch for {artist_id}")
            if not START <= year <= END or year in sparse[artist_id]:
                raise ValueError(f"Line {lineno}: invalid or duplicate artist-year {artist_id}/{year}")
            matches = int(rec["match_count"])
            volumes = int(rec["volume_count"])
            if matches <= 0 or volumes < 0 or volumes > matches:
                raise ValueError(f"Line {lineno}: invalid match/volume count")
            if matches > totals[(int(artist["n"]), year)]:
                raise ValueError(f"Line {lineno}: query count exceeds corpus total")
            sparse[artist_id][year] = matches
            rows_count += 1
    expected_hash = report.get("data_sha256")
    if expected_hash and sha256(records_file) != expected_hash:
        raise ValueError("Source JSONL SHA-256 differs from extraction report")
    if rows_count != int(report["row_count"]):
        raise ValueError(f"Expected {report['row_count']} rows, found {rows_count}")
    for artist_id, a in by_artist.items():
        if len(sparse.get(artist_id, {})) != int(a.get("rows", -1)):
            raise ValueError(f"Row count mismatch for {artist_id}")
        if sum(sparse.get(artist_id, {}).values()) != int(a.get("match_count_sum", -1)):
            raise ValueError(f"Match sum mismatch for {artist_id}")
    return artists, sparse, totals, {"artist_file": sha256(artists_file),
                                     "data_file": sha256(records_file),
                                     "report_file": sha256(report_file)}, report


def make_decade_features(artists, sparse, totals, *, min_nonzero_decades: int,
                         min_total_matches: int, assume_unlisted_zero: bool):
    if not assume_unlisted_zero:
        raise ValueError("Pass --assume-unlisted-zero explicitly; read README_TASK5.md")
    rows, included, excluded = [], [], []
    for a in artists:
        artist_id = str(a["qid"])
        observations = sparse.get(artist_id, {})
        n = int(a["n"])
        birth = a.get("birth_year")
        birth = int(birth) if birth not in (None, "") else None
        prebirth = sum(c for y, c in observations.items() if birth is not None and y < birth)
        rates, count_decades = [], []
        for dec in DECADES:
            total = sum(totals[(n, y)] for y in range(dec, dec + 10))
            matches = sum(observations.get(y, 0) for y in range(dec, dec + 10))
            rates.append(1e9 * matches / total)
            count_decades.append(matches)
        total_matches = sum(observations.values())
        nonzero_decades = sum(v > 0 for v in count_decades)
        row = {"artist_id": artist_id, "canonical_name": a["query"],
               "n": n, "movements": ";".join(a.get("movements") or []),
               "birth_year": "" if birth is None else birth,
               "extraction_status": a["status"],
               "observed_year_rows": len(observations),
               "total_match_count": total_matches,
               "nonzero_decades": nonzero_decades,
               "prebirth_match_count": prebirth,
               "peak_decade": DECADES[int(np.argmax(rates))] if any(rates) else ""}
        row.update({f"rate_{dec}": r for dec, r in zip(DECADES, rates)})
        rows.append(row)
        reasons = []
        if a["status"] == "not_found": reasons.append("exact_name_not_found")
        if a["status"] == "matched" and nonzero_decades < min_nonzero_decades:
            reasons.append("too_few_nonzero_decades")
        if a["status"] == "matched" and total_matches < min_total_matches:
            reasons.append("too_few_total_matches")
        if a["status"] == "matched" and np.std(np.log1p(rates)) < 1e-12:
            reasons.append("constant_shape")
        if reasons:
            excluded.append({**row, "exclusion_reason": ";".join(reasons)})
        else:
            included.append(row)
    return rows, included, excluded


def normalized_shape_matrix(rows: list[dict]) -> np.ndarray:
    rates = np.array([[float(row[f"rate_{d}"]) for d in DECADES] for row in rows], dtype=float)
    if not np.isfinite(rates).all() or (rates < 0).any():
        raise ValueError("Invalid non-finite/negative rate")
    transformed = np.log1p(rates)
    mean = transformed.mean(axis=1, keepdims=True)
    scale = transformed.std(axis=1, keepdims=True)
    if (scale < 1e-12).any():
        raise ValueError("Constant time series cannot be shape clustered")
    return (transformed - mean) / scale


def cluster_and_evaluate(x: np.ndarray, *, max_k: int, min_cluster_size: int,
                         random_state: int, stability_runs: int):
    if len(x) < max(2 * min_cluster_size, 4):
        raise ValueError(f"Only {len(x)} eligible artists; reduce filters/min_cluster_size")
    k_max = min(max_k, len(x) - 1)
    k_values = range(2, k_max + 1)
    outcome = []
    models = {}
    for k in k_values:
        labels_runs = []
        first = None
        for i in range(stability_runs):
            km = KMeans(n_clusters=k, random_state=random_state + i * 13,
                        n_init=20, max_iter=500, algorithm="lloyd")
            labels = km.fit_predict(x)
            if i == 0: first = km
            labels_runs.append(labels)
        sizes = Counter(labels_runs[0])
        minimum = min(sizes.values())
        eligible = minimum >= min_cluster_size
        sil = float(silhouette_score(x, labels_runs[0]))
        aris = [adjusted_rand_score(a, b) for a, b in combinations(labels_runs, 2)]
        stability = float(np.mean(aris)) if aris else 1.0
        models[k] = first
        outcome.append({"k": k, "silhouette_score": sil,
                        "mean_pairwise_ARI": stability,
                        "inertia": float(first.inertia_),
                        "min_cluster_size": minimum,
                        "cluster_sizes": ";".join(map(str, sorted(sizes.values()))),
                        "size_constraint_pass": eligible})
    valid = [r for r in outcome if r["size_constraint_pass"]]
    if not valid:
        raise ValueError("No K satisfies min cluster size. Inspect metrics or reduce --min-cluster-size")
    chosen = sorted(valid, key=lambda r: (-r["silhouette_score"], -r["mean_pairwise_ARI"], r["k"]))[0]
    for r in outcome:
        r["selected"] = r["k"] == chosen["k"]
    return chosen, outcome, models[chosen["k"]]


def plot_reports(metrics, included, x, model, output: Path):
    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    ks = [r["k"] for r in metrics]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.plot(ks, [r["silhouette_score"] for r in metrics], marker="o")
    ax.set(title="K-Means: Silhouette by K (shape features)", xlabel="Number of clusters K",
           ylabel="Silhouette score", xticks=ks)
    fig.tight_layout()
    fig.savefig(figures / "k_selection.png", dpi=170)
    plt.close(fig)

    labs = model.labels_
    fig, ax = plt.subplots(figsize=(10, 5.3))
    for cluster in sorted(set(labs)):
        ax.plot(DECADES, model.cluster_centers_[cluster], marker=".",
                label=f"Cluster {cluster} (n={sum(labs == cluster)})")
    ax.set(title="Cluster centers: relative visibility shapes",
           xlabel="Decade starting", ylabel="Within-artist standardized log frequency")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / "cluster_centers.png", dpi=170)
    plt.close(fig)

    for cluster in sorted(set(labs)):
        inds = np.where(labs == cluster)[0]
        fig, ax = plt.subplots(figsize=(10, 5))
        # Cluster samples shown without assigning a semantic name in advance.
        for ix in inds[:min(25, len(inds))]:
            ax.plot(DECADES, x[ix], alpha=0.15, linewidth=0.8)
        ax.plot(DECADES, model.cluster_centers_[cluster], linewidth=2.5,
                label="Centroid")
        ax.set(title=f"Cluster {cluster}: up to 25 sampled artist shapes (n={len(inds)})",
               xlabel="Decade starting", ylabel="Within-artist standardized log frequency")
        ax.legend()
        fig.tight_layout()
        fig.savefig(figures / f"cluster_{cluster}_trajectories.png", dpi=170)
        plt.close(fig)


def run(args):
    root = Path(args.root).resolve()
    output = (root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    if not args.assume_unlisted_zero:
        raise ValueError("Required scientific assumption: pass --assume-unlisted-zero explicitly")
    artists, sparse, totals, hashes, report = load_inputs(root)
    all_rows, included, excluded = make_decade_features(
        artists, sparse, totals, min_nonzero_decades=args.min_nonzero_decades,
        min_total_matches=args.min_total_matches,
        assume_unlisted_zero=args.assume_unlisted_zero)
    if not included:
        raise ValueError("No eligible artists, lower eligibility thresholds")
    x = normalized_shape_matrix(included)
    chosen, metrics, model = cluster_and_evaluate(x, max_k=args.max_k,
        min_cluster_size=args.min_cluster_size, random_state=args.seed,
        stability_runs=args.stability_runs)
    output.mkdir(parents=True, exist_ok=True)
    ratecols = [f"rate_{d}" for d in DECADES]
    common = ["artist_id", "canonical_name", "n", "movements", "birth_year",
              "extraction_status", "observed_year_rows", "total_match_count",
              "nonzero_decades", "prebirth_match_count", "peak_decade"]
    write_csv(output / "artist_features.csv", all_rows, common + ratecols)
    write_csv(output / "artist_exclusions.csv", excluded, common + ["exclusion_reason"] + ratecols)
    labels = model.labels_.tolist()
    assigned = []
    for row, lab in zip(included, labels):
        d = {k: row[k] for k in common}
        d["cluster_id"] = int(lab)
        assigned.append(d)
    write_csv(output / "cluster_assignments.csv", assigned, common + ["cluster_id"])
    write_csv(output / "clustering_metrics.csv", metrics,
              ["k", "silhouette_score", "mean_pairwise_ARI", "inertia",
               "min_cluster_size", "cluster_sizes", "size_constraint_pass", "selected"])
    center_rows = []
    for cluster in sorted(set(labels)):
        row = {"cluster_id": cluster, "artists": labels.count(cluster)}
        row.update({f"z_{dec}": float(v) for dec, v in zip(DECADES, model.cluster_centers_[cluster])})
        center_rows.append(row)
    write_csv(output / "cluster_centers.csv", center_rows,
              ["cluster_id", "artists"] + [f"z_{d}" for d in DECADES])
    plot_reports(metrics, included, x, model, output)
    summary = {
        "study": "Ngram exact-full-name, English 20200217; 1800-2019",
        "classification": "K-Means on 22 within-artist z-scored log1p decade corpus-normalized frequencies",
        "normalization": "sum decade match_count / sum matching n-gram decade totalcounts * 1e9",
        "sparse_year_assumption": "Unlisted years are treated as zero recorded exact-name matches ONLY for artists marked matched in complete extraction. This is a modelling assumption, not proof of zero historical visibility.",
        "unmatched_artist_policy": "Excluded from clustering; reported in artist_exclusions.csv",
        "prebirth_year_policy": "Retained and flagged in prebirth_match_count; not automatically deleted",
        "all_artists": len(artists),
        "matched_artists": sum(a["status"] == "matched" for a in artists),
        "not_found_artists": sum(a["status"] == "not_found" for a in artists),
        "eligible_artists": len(included),
        "excluded_artists": len(excluded),
        "excluded_reasons": dict(Counter(reason for row in excluded
                              for reason in row["exclusion_reason"].split(";") if reason)),
        "n_source_rows": int(report["row_count"]),
        "k_selected": chosen["k"],
        "selected_silhouette": chosen["silhouette_score"],
        "selected_stability_mean_pairwise_ARI": chosen["mean_pairwise_ARI"],
        "cluster_sizes": dict(sorted(Counter(map(int, labels)).items())),
        "parameters": {k: getattr(args, k) for k in ("max_k", "min_cluster_size", "min_nonzero_decades", "min_total_matches", "seed", "stability_runs", "assume_unlisted_zero")},
        "input_sha256": hashes,
        "limitations": [
            "Cluster IDs have no intrinsic semantic ordering or predefined art-history meaning.",
            "Silhouette is an internal descriptive metric, not proof of historical causal patterns.",
            "Exact-name queries may contain false matches, OCR/date errors and exclude aliases.",
            "Comparing different n-gram orders is an operational frequency proxy, not perfect cross-name equivalence.",
            "Decade binning smooths peaks and clustering over absolute dates may reflect era/birth cohort.",
            "Sparse years are explicitly zero-filled under the complete-extraction assumption."
        ]
    }
    (output / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"eligible_artists": len(included), "k_selected": chosen["k"],
                      "silhouette": round(chosen["silhouette_score"], 4),
                      "stability_mean_ARI": round(chosen["mean_pairwise_ARI"], 4),
                      "cluster_sizes": summary["cluster_sizes"], "output": str(output)},
                     indent=2, ensure_ascii=False))
    if chosen["mean_pairwise_ARI"] < 0.7:
        print("WARNING: LOW CLUSTER STABILITY (mean pairwise ARI < 0.7): interpret cautiously", file=sys.stderr)
    return summary


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".", help="Repository root (default: current directory)")
    p.add_argument("--output", default="results/ml/ngram_kmeans", help="Output directory")
    p.add_argument("--assume-unlisted-zero", action="store_true",
                   help="REQUIRED: explicit sparse-to-zero modelling assumption for matched artists")
    p.add_argument("--min-nonzero-decades", type=int, default=4)
    p.add_argument("--min-total-matches", type=int, default=20)
    p.add_argument("--min-cluster-size", type=int, default=10)
    p.add_argument("--max-k", type=int, default=6)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--stability-runs", type=int, default=5)
    a = p.parse_args(argv)
    if a.min_nonzero_decades < 1 or a.min_total_matches < 1 or a.min_cluster_size < 2 or a.max_k < 2 or a.stability_runs < 2:
        p.error("Thresholds must be positive (min cluster size, max K, stability runs >=2)")
    return a


if __name__ == "__main__":
    try:
        run(parse_args())
    except (ValueError, FileNotFoundError) as exc:
        sys.exit(f"ERROR: {exc}")
